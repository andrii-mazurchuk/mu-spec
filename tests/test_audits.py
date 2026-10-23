"""record_audit: every audit run leaves a trace, a pass as much as a flag.

Refs only. Nothing here judges, scores or gates -- it checks that what the
caller names exists, and writes it down.
"""

from __future__ import annotations

import json

import pytest

from mu_spec.server import _tools, handle
from mu_spec.storage import ProjectStore, Slice


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    return d


def call(store, prompts, method, path, body=None):
    status, _, raw = handle(method, path, store, prompts, body, now_fn=lambda: 7.0)
    return status, json.loads(raw)


def seed(store, prompts):
    _, msg = call(store, prompts, "POST", "/inbox",
                  {"type": "initiate", "title": "a shop", "project": "m"})
    mid = msg["message_id"]
    call(store, prompts, "POST", "/projects", {"project": "m", "in_response_to": mid})
    call(store, prompts, "POST", "/projects/m/amendments",
         {"in_response_to": mid, "entries": [{"layer": "I", "title": "buyers buy"}]})
    manifest = store.load_manifest("m")
    manifest.slices["s"] = Slice(name="s")
    store.save_manifest("m", manifest)
    call(store, prompts, "POST", "/projects/m/issues",
         {"target": "I-01", "kind": "additive", "claim": "missing a refund"})


PASS = {"scope": ["I-01", "slice:s"], "layer": "I", "questions": ["I1", "I2"],
        "outcome": "pass", "issues": [], "auditor": "auditor-a", "run_id": "r1",
        "actor": "agent-7"}


def audits(store, prompts):
    _, payload = call(store, prompts, "GET", "/projects/m/events?kind=audit")
    return payload["events"]


def test_a_pass_is_recorded(store, prompts):
    seed(store, prompts)
    status, payload = call(store, prompts, "POST", "/projects/m/audits", PASS)
    assert status == 201
    rows = audits(store, prompts)
    assert len(rows) == 1
    event = rows[0]
    assert event["refs"] == ["I-01", "slice:s"]
    assert event["facts"] == {
        "layer": "I", "questions": ["I1", "I2"], "outcome": "pass",
        "issues": [], "auditor": "auditor-a", "run_id": "r1", "by": "agent-7",
    }
    assert payload["seq"] == event["seq"]


def test_a_flag_records_its_issues_as_refs(store, prompts):
    seed(store, prompts)
    body = {**PASS, "outcome": "flagged", "issues": ["iss-0001"]}
    status, _ = call(store, prompts, "POST", "/projects/m/audits", body)
    assert status == 201
    event = audits(store, prompts)[0]
    assert event["refs"] == ["I-01", "slice:s", "iss-0001"]
    assert event["facts"]["issues"] == ["iss-0001"]


@pytest.mark.parametrize("patch,needle", [
    ({"scope": []}, "scope"),
    ({"scope": "I-01"}, "scope"),
    ({"scope": ["I-09"]}, "I-09"),
    ({"scope": ["slice:nope"]}, "nope"),
    ({"scope": ["banana"]}, "banana"),
    ({"layer": "Q"}, "layer"),
    ({"questions": "I1"}, "questions"),
    ({"outcome": "maybe"}, "outcome"),
    ({"outcome": "flagged", "issues": []}, "issues"),
    ({"outcome": "pass", "issues": ["iss-0001"]}, "issues"),
    ({"outcome": "flagged", "issues": ["iss-0099"]}, "iss-0099"),
    ({"auditor": 3}, "auditor"),
])
def test_refusals_are_400_and_name_the_problem(store, prompts, patch, needle):
    seed(store, prompts)
    status, payload = call(store, prompts, "POST", "/projects/m/audits",
                           {**PASS, **patch})
    assert status == 400
    assert needle in payload["error"]
    assert audits(store, prompts) == []


def test_an_issue_from_another_project_does_not_count(store, prompts):
    seed(store, prompts)
    _, msg = call(store, prompts, "POST", "/inbox",
                  {"type": "initiate", "title": "other", "project": "o"})
    call(store, prompts, "POST", "/projects",
         {"project": "o", "in_response_to": msg["message_id"]})
    manifest = store.load_manifest("o")
    manifest.slices["x"] = Slice(name="x")
    store.save_manifest("o", manifest)
    status, payload = call(store, prompts, "POST", "/projects/o/audits",
                           {**PASS, "scope": ["slice:x"], "layer": "final",
                            "outcome": "flagged", "issues": ["iss-0001"]})
    assert status == 400
    assert "iss-0001" in payload["error"]


def test_an_unknown_project_is_404(store, prompts):
    status, _ = call(store, prompts, "POST", "/projects/nope/audits", PASS)
    assert status == 404


def test_the_tool_tells_an_agent_to_record_a_pass():
    tool = next(t for t in _tools() if t["name"] == "record_audit")
    assert tool["method"] == "POST"
    assert tool["path"] == "/projects/{project}/audits"
    assert "pass" in tool["description"].lower()
    assert set(tool["input_schema"]["required"]) == {
        "project", "scope", "layer", "outcome"}
