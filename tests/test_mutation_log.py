"""The change log: every write leaves a record of who made it and what it was.

Agents cut and ship on their own. When one of them judges wrong, this is
what traces the damage back to the request that did it.
"""

from __future__ import annotations

import json
import re

import pytest

from mu_spec import lifecycle as lc
from mu_spec.lifecycle import Lifecycle
from mu_spec.server import _ROUTES, handle
from mu_spec.storage import ProjectStore, Slice


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    return d


def call(store, prompts, method, path, body=None, **kw):
    status, _, raw = handle(method, path, store, prompts, body, now_fn=lambda: 7.0, **kw)
    return status, json.loads(raw)


def mutations(store, since=0):
    return Lifecycle(store.events_path()).list(kind=lc.MUTATION, since=since)


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
    return mid


_SAMPLE = {"project": "m", "slice": "s", "iid": "iss-0001", "mid": "msg-0001",
           "entry": "I-01", "id": "I-01"}


def _concrete(pattern: str) -> str:
    path = pattern.strip("^$")
    return re.sub(r"\(\?P<(\w+)>[^)]*\)\??", lambda m: _SAMPLE[m.group(1)], path)


POST_ROUTES = [(name, _concrete(p.pattern)) for m, p, name in _ROUTES if m == "POST"]


@pytest.mark.parametrize("name,path", POST_ROUTES, ids=[n for n, _ in POST_ROUTES])
def test_every_post_route_leaves_exactly_one_mutation_event(
    store, prompts, monkeypatch, name, path
):
    """Parametrized over the route table, so a new route is covered the day
    it is added -- and a route that slips past the log fails here."""
    monkeypatch.delenv("MU_SPEC_GITHUB_TOKEN", raising=False)
    seed(store, prompts)
    mark = mutations(store)[-1].seq
    status, _ = call(store, prompts, "POST", path, {"actor": "agent-7"})
    rows = mutations(store, since=mark)
    assert len(rows) == 1
    assert rows[0].facts["route"] == name
    assert rows[0].facts["actor"] == "agent-7"
    assert rows[0].facts["status"] == status
    assert rows[0].at == 7.0


def test_every_write_tool_tells_an_agent_to_send_actor():
    from mu_spec.server import _tools

    writes = [t for t in _tools() if t["method"] == "POST"]
    assert writes
    assert all("actor" in t["input_schema"]["properties"] for t in writes)


def test_a_refused_request_is_logged_too(store, prompts):
    status, _ = call(store, prompts, "POST", "/projects", {"actor": "a"})
    assert status == 400
    (row,) = mutations(store)
    assert row.facts["status"] == 400
    assert "error" in row.facts["result"]


def test_actor_falls_back_to_the_header_then_unknown(store, prompts):
    call(store, prompts, "POST", "/inbox", {"type": "x"}, actor="from-header")
    call(store, prompts, "POST", "/inbox", {"type": "x"})
    call(store, prompts, "POST", "/inbox", {"type": "x", "actor": "body"}, actor="hdr")
    assert [r.facts["actor"] for r in mutations(store)] == ["from-header", "unknown", "body"]


def test_reads_are_not_logged(store, prompts):
    seed(store, prompts)
    before = len(mutations(store))
    call(store, prompts, "GET", "/projects/m/spine")
    assert len(mutations(store)) == before


def test_a_large_body_is_truncated(store, prompts):
    call(store, prompts, "POST", "/inbox", {"type": "x", "body": "y" * 10_000})
    (row,) = mutations(store)
    assert isinstance(row.facts["body"], str)
    assert len(row.facts["body"]) <= 2100


def test_mutations_read_back_through_the_events_route(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/events?kind=mutation")
    assert [e["facts"]["route"] for e in payload["events"]][-1] == "amendment"


def test_mutations_with_no_project_read_back_unit_wide(store, prompts):
    """An unrouted POST, or an initiate naming no project, logs with
    project=None -- which no per-project read can ever return."""
    seed(store, prompts)
    mark = mutations(store)[-1].seq
    call(store, prompts, "POST", "/nowhere", {"actor": "lost"})
    call(store, prompts, "POST", "/inbox", {"type": "initiate", "title": "t"})
    _, payload = call(store, prompts, "GET", f"/events?kind=mutation&since={mark}")
    rows = payload["events"]
    assert [(e["project"], e["facts"]["route"]) for e in rows] == [
        (None, "unrouted"), (None, "post_inbox")]
    assert payload["next_since"] == rows[-1]["seq"]
    _, everything = call(store, prompts, "GET", "/events?kind=mutation")
    assert {e["project"] for e in everything["events"]} == {None, "m"}
    status, _ = call(store, prompts, "GET", "/events?since=x")
    assert status == 400


# -- before and after, for the manifest setters --------------------------------


def test_set_repo_carries_what_it_replaced(store, prompts):
    seed(store, prompts)
    call(store, prompts, "POST", "/projects/m/repo", {"repo": "a/one"})
    call(store, prompts, "POST", "/projects/m/repo", {"repo": "a/two"})
    row = mutations(store)[-1]
    assert row.facts["was"] == {"repo": "a/one"}
    assert row.facts["result"]["repo"] == "a/two"


def test_set_labels_carries_labels_and_pickup_it_replaced(store, prompts):
    seed(store, prompts)
    call(store, prompts, "POST", "/projects/m/labels", {"labels": ["x"], "pickup": ["p"]})
    call(store, prompts, "POST", "/projects/m/labels", {"labels": ["y"], "pickup": []})
    row = mutations(store)[-1]
    assert row.facts["was"]["labels"] == ["x"]
    assert row.facts["was"]["pickup"] == ["p"]
    assert row.facts["result"]["pickup"] == []


def test_declare_module_carries_what_it_replaced(store, prompts):
    seed(store, prompts)
    call(store, prompts, "POST", "/projects/m/modules", {"path": "a.py", "implements": []})
    row = mutations(store)[-1]
    assert row.facts["was"] == {"path": "a.py", "implements": []}


def test_ratify_and_reject_carry_the_state_they_replaced(store, prompts):
    seed(store, prompts)
    for note in ("first no", "second no"):
        call(store, prompts, "POST", "/projects/m/slicing/proposal", {"proposal": {"t": ["I-01"]}})
        call(store, prompts, "POST", "/projects/m/slicing/proposal/reject", {"note": note})
    rejected = [r for r in mutations(store) if r.facts["route"] == "reject_proposal"]
    # Both survive here; the proposal file keeps only the last.
    assert [r.facts["body"]["note"] for r in rejected] == ["first no", "second no"]
    assert rejected[0].facts["was"]["proposal"]["proposal"] == {"t": ["I-01"]}
    call(store, prompts, "POST", "/projects/m/slicing/proposal", {"proposal": {"t": ["I-01"]}})
    status, _ = call(store, prompts, "POST", "/projects/m/slicing/proposal/ratify", {})
    assert status == 200
    ratified = mutations(store)[-1]
    assert ratified.facts["was"]["slices"] == {"s": []}


# -- closing keeps history ---------------------------------------------------


def test_close_issue_appends_instead_of_rewriting(store, prompts):
    seed(store, prompts)
    call(store, prompts, "POST", "/projects/m/issues",
         {"target": "I-01", "kind": "additive", "claim": "c", "raised_by": "x"})
    status, closed = call(store, prompts, "POST", "/projects/m/issues/iss-0001/close",
                          {"status": "resolved", "note": "done", "actor": "fixer"})
    assert status == 200
    assert closed["status"] == "resolved"
    assert closed["resolution"]["by"] == "fixer"
    assert closed["resolution"]["at"] == 7.0
    lines = store.issues_path().read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["status"] for l in lines] == ["open", "resolved"]
    _, listed = call(store, prompts, "GET", "/projects/m/issues")
    assert [i["status"] for i in listed["issues"]] == ["resolved"]
    (event,) = Lifecycle(store.events_path()).list(kind=lc.ISSUE_CLOSED)
    assert event.refs == ("iss-0001", "I-01")
    assert event.facts["by"] == "fixer"


def test_resolve_message_appends_instead_of_rewriting(store, prompts):
    mid = seed(store, prompts)
    _, msg = call(store, prompts, "POST", "/inbox", {"type": "comment", "title": "hm", "project": "m"})
    status, resolved = call(store, prompts, "POST", f"/inbox/{msg['message_id']}/resolve",
                            {"status": "rejected", "note": "no"}, actor="human")
    assert status == 200
    assert resolved["resolution"]["by"] == "human"
    assert resolved["resolution"]["at"] == 7.0
    rows = [json.loads(l) for l in store.inbox_path().read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows if r["id"] == msg["message_id"]] == ["pending", "rejected"]
    # record_produced appends too: the initiate message's history is all there.
    assert len([r for r in rows if r["id"] == mid]) >= 2
    _, listed = call(store, prompts, "GET", "/inbox?status=rejected")
    assert [m["id"] for m in listed["messages"]] == [msg["message_id"]]


# -- logging never breaks a request --------------------------------------------


def test_a_failing_event_write_does_not_fail_the_request(store, prompts):
    store.events_path().parent.mkdir(parents=True, exist_ok=True)
    store.events_path().mkdir()  # appending to a directory raises
    status, payload = call(store, prompts, "POST", "/inbox",
                           {"type": "initiate", "title": "t", "project": "m"})
    assert status == 201
    assert payload["message_id"] == "msg-0001"
