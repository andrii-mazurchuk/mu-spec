"""M18: a write that timed out but landed can be retried safely.

submit_amendment and raise_issue take an optional `idempotency_key`. A repeat
with the same key in the same project returns the original result and writes
nothing; the same key with a different body is a 409.
"""

from __future__ import annotations

import json

import pytest

from mu_spec.server import _tools, handle
from mu_spec.storage import ProjectStore


@pytest.fixture()
def root(tmp_path):
    return tmp_path / "projects"


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    return d


def call(root, prompts, method, path, body=None):
    # A fresh store per call: every call is what a restarted server would see.
    status, _, raw = handle(method, path, ProjectStore(root), prompts, body,
                            now_fn=lambda: 7.0)
    return status, json.loads(raw)


def project(root, prompts, name):
    _, msg = call(root, prompts, "POST", "/inbox",
                  {"type": "initiate", "title": "a shop", "project": name})
    mid = msg["message_id"]
    assert call(root, prompts, "POST", "/projects",
                {"project": name, "in_response_to": mid})[0] == 201
    return mid


def footprint(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "lifecycle.jsonl"}


def intent(root, prompts, name, mid, key, title="buyers buy"):
    return call(root, prompts, "POST", f"/projects/{name}/amendments",
                {"in_response_to": mid, "entries": [{"layer": "I", "title": title}],
                 "idempotency_key": key})


def test_a_replayed_amendment_returns_the_original_ids_and_writes_nothing(root, prompts):
    mid = project(root, prompts, "m")
    status, first = intent(root, prompts, "m", mid, "k-1")
    assert status == 200 and first["created"] == ["I-01"], first
    before = footprint(root)
    status, again = intent(root, prompts, "m", mid, "k-1")
    assert status == 200
    assert again["created"] == ["I-01"] and again["replayed"] is True
    assert {k: v for k, v in again.items() if k != "replayed"} == first
    assert footprint(root) == before, "a replay wrote"
    assert len(ProjectStore(root).load_all("m")) == 1


def test_the_same_key_with_a_different_body_is_a_409(root, prompts):
    mid = project(root, prompts, "m")
    intent(root, prompts, "m", mid, "k-1")
    before = footprint(root)
    status, out = intent(root, prompts, "m", mid, "k-1", title="sellers sell")
    assert status == 409, out
    assert "k-1" in out["error"]
    assert footprint(root) == before


def test_who_sent_it_is_not_part_of_the_body(root, prompts):
    mid = project(root, prompts, "m")
    body = {"in_response_to": mid, "entries": [{"layer": "I", "title": "t"}],
            "idempotency_key": "k"}
    call(root, prompts, "POST", "/projects/m/amendments", {**body, "actor": "a"})
    status, out = call(root, prompts, "POST", "/projects/m/amendments", {**body, "actor": "b"})
    assert status == 200 and out["replayed"] is True


def test_keys_are_per_project(root, prompts):
    m = project(root, prompts, "m")
    n = project(root, prompts, "n")
    assert intent(root, prompts, "m", m, "same")[1]["created"] == ["I-01"]
    status, out = intent(root, prompts, "n", n, "same", title="sellers sell")
    assert status == 200 and out["created"] == ["I-01"] and "replayed" not in out
    assert len(ProjectStore(root).load_all("n")) == 1


def test_a_refused_amendment_is_not_remembered(root, prompts):
    mid = project(root, prompts, "m")
    body = {"in_response_to": mid, "idempotency_key": "k",
            "entries": [{"layer": "I", "title": "b", "depends_on": ["I-01"]}]}
    status, out = call(root, prompts, "POST", "/projects/m/amendments", body)
    assert status == 409 and not out["admitted"]
    # Nothing landed, so the key is free for the call that will.
    intent(root, prompts, "m", mid, "k2")
    status, out = call(root, prompts, "POST", "/projects/m/amendments", body)
    assert status == 200 and out["admitted"] and "replayed" not in out


def test_a_replayed_issue_returns_the_original_id_and_writes_nothing(root, prompts):
    mid = project(root, prompts, "m")
    intent(root, prompts, "m", mid, "k-i")
    body = {"target": "I-01", "kind": "semantic", "claim": "wrong",
            "idempotency_key": "iss-key"}
    status, first = call(root, prompts, "POST", "/projects/m/issues", body)
    assert status == 201, first
    before = footprint(root)
    status, again = call(root, prompts, "POST", "/projects/m/issues", body)
    assert status == 201 and again["id"] == first["id"] and again["replayed"] is True
    assert footprint(root) == before
    status, out = call(root, prompts, "POST", "/projects/m/issues",
                       {**body, "claim": "different"})
    assert status == 409, out


def test_a_key_must_be_a_non_empty_string(root, prompts):
    mid = project(root, prompts, "m")
    status, out = intent(root, prompts, "m", mid, "  ")
    assert status == 400, out


def test_tools_tell_agents_to_send_a_key_on_every_retryable_write():
    tools = {t["name"]: t for t in _tools()}
    for name in ("submit_amendment", "raise_issue"):
        assert tools[name]["input_schema"]["properties"]["idempotency_key"] == {"type": "string"}
        assert "idempotency_key" in tools[name]["description"]
