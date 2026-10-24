"""An amendment may cite an issue instead of an inbox message.

The issue names its target entry, so where the fix may enter is mechanical:
at the target's layer. Above it is not a repair any more -- it is the
issue's own premise being wrong, and that is escalated, not patched.
"""

from __future__ import annotations

import json

import pytest

from mu_spec import lifecycle as lc
from mu_spec.lifecycle import Lifecycle
from mu_spec.server import handle
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


@pytest.fixture()
def project(store, prompts):
    """I-01 <- B-01, slice `s` ratified, and iss-0001 filed against B-01."""
    _, msg = call(store, prompts, "POST", "/inbox",
                  {"type": "initiate", "title": "a shop", "project": "m"})
    mid = msg["message_id"]
    call(store, prompts, "POST", "/projects", {"project": "m", "in_response_to": mid})
    for entry in ({"layer": "I", "title": "buyers buy"},
                  {"layer": "B", "title": "a buyer pays", "derives_from": ["I-01"]}):
        status, out = call(store, prompts, "POST", "/projects/m/amendments",
                           {"in_response_to": mid, "entries": [entry]})
        assert status == 200, out
    manifest = store.load_manifest("m")
    manifest.slices["s"] = Slice(name="s")
    store.save_manifest("m", manifest)
    status, issue = call(store, prompts, "POST", "/projects/m/issues",
                         {"target": "B-01", "kind": "semantic",
                          "claim": "paying needs a currency"})
    assert status == 201, issue
    return {"mid": mid, "iid": issue["id"]}


def amend(store, prompts, cite, entries, **kw):
    return call(store, prompts, "POST", "/projects/m/amendments",
                {"in_response_to": cite, "entries": entries, **kw})


FIX_B = {"layer": "B", "title": "a buyer pays in a currency",
         "derives_from": ["I-01"], "supersedes": "B-01"}


def issue(store, prompts, iid):
    _, out = call(store, prompts, "GET", "/projects/m/issues")
    return next(i for i in out["issues"] if i["id"] == iid)


def test_an_issue_cited_fix_at_the_targets_layer_is_admitted(store, prompts, project):
    status, out = amend(store, prompts, project["iid"], [FIX_B])
    assert status == 200, out
    assert out["created"] == ["B-02"]
    assert out["in_response_to"] == project["iid"]
    # Recorded on the issue, which stays open so propagation can cite it too.
    after = issue(store, prompts, project["iid"])
    assert after["status"] == "open"
    assert after["resolution"]["produced"] == ["B-02"]
    derivation = Lifecycle(store.events_path()).list(kind=lc.DERIVATION)[-1]
    assert derivation.facts["in_response_to"] == project["iid"]
    assert derivation.facts["request_type"] == "issue"
    correction = Lifecycle(store.events_path()).list(kind=lc.CORRECTION)[-1]
    assert correction.facts["in_response_to"] == project["iid"]


def test_a_fix_above_the_target_is_refused_with_an_escalate_hint(store, prompts, project):
    status, out = amend(store, prompts, project["iid"],
                        [{"layer": "I", "title": "buyers buy in a currency",
                          "supersedes": "I-01"}])
    assert status == 400
    assert "escalate" in out["error"]
    assert issue(store, prompts, project["iid"])["resolution"] is None


def test_a_fix_starting_below_the_target_is_refused(store, prompts, project):
    status, out = amend(store, prompts, project["iid"],
                        [{"layer": "A", "title": "a ledger", "derives_from": ["B-01"]}],
                        slice="s")
    assert status == 400
    assert "B-01" in out["error"]


def test_after_the_fix_lands_propagation_below_may_cite_the_issue(store, prompts, project):
    amend(store, prompts, project["iid"], [FIX_B])
    status, out = amend(store, prompts, project["iid"],
                        [{"layer": "A", "title": "a ledger", "derives_from": ["B-02"]}],
                        slice="s")
    assert status == 200, out
    assert issue(store, prompts, project["iid"])["resolution"]["produced"] == ["B-02", "A-01"]


@pytest.mark.parametrize("closing", [
    {"status": "resolved", "note": "done"},
    {"status": "rejected", "note": "not a fault"},
])
def test_a_closed_issue_cannot_be_cited(store, prompts, project, closing):
    call(store, prompts, "POST", f"/projects/m/issues/{project['iid']}/close", closing)
    status, out = amend(store, prompts, project["iid"], [FIX_B])
    assert status == 400
    assert closing["status"] in out["error"]


def test_an_escalated_issue_can_still_be_cited(store, prompts, project):
    call(store, prompts, "POST", f"/projects/m/issues/{project['iid']}/close",
         {"status": "escalated", "note": "needs a ruling"})
    status, out = amend(store, prompts, project["iid"], [FIX_B])
    assert status == 200, out


def test_an_unknown_or_foreign_issue_is_refused(store, prompts, project):
    status, out = amend(store, prompts, "iss-0099", [FIX_B])
    assert status == 400 and "iss-0099" in out["error"]
    call(store, prompts, "POST", "/projects", {"project": "n",
                                               "in_response_to": project["mid"]})
    status, out = call(store, prompts, "POST", "/projects/n/amendments",
                       {"in_response_to": project["iid"],
                        "entries": [{"layer": "I", "title": "x"}]})
    assert status == 400 and "project" in out["error"]


def test_the_inbox_path_is_unchanged(store, prompts, project):
    """A correction still may not enter below behaviour, and its produced
    list lands on the message, not on any issue."""
    _, msg = call(store, prompts, "POST", "/inbox",
                  {"type": "correction", "title": "wrong", "project": "m"})
    status, out = amend(store, prompts, msg["message_id"],
                        [{"layer": "A", "title": "a ledger", "derives_from": ["B-01"]}],
                        slice="s")
    assert status == 400
    status, out = amend(store, prompts, msg["message_id"], [FIX_B])
    assert status == 200, out
    assert issue(store, prompts, project["iid"])["resolution"] is None
