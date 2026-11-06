"""A fork: a project copied to a new name, so a benchmark run starts from a
frozen corpus and nothing it records reaches the next run."""

from __future__ import annotations

import pytest

from mu_spec.storage import ProjectStore
from test_server import call, post, seed


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "default.md").write_text("# mu-spec\n", encoding="utf-8")
    return d


def fork(store, prompts, name="m-run1", source="m", mid=None):
    if mid is None:
        _, msg = post(store, prompts, "initiate", "a run", project=name)
        mid = msg["message_id"]
    return call(store, prompts, "POST", f"/projects/{source}/fork",
                {"name": name, "in_response_to": mid})


def ids(store, project):
    return sorted(str(e.id) for e in store.load_all(project))


def test_a_fork_is_the_same_graph_under_a_new_name(store, prompts):
    seed(store, prompts)
    status, out = fork(store, prompts)
    assert status == 201
    assert out["project"] == "m-run1" and out["source"] == "m"
    assert "m-run1" in store.list_projects()
    assert store.load_manifest("m-run1").project == "m-run1"
    assert ids(store, "m-run1") == ids(store, "m")


def test_a_fork_keeps_numbering_and_writes_stay_on_its_own_side(store, prompts):
    mid = seed(store, prompts)
    fork(store, prompts)
    before = ids(store, "m")
    status, out = call(store, prompts, "POST", "/projects/m-run1/amendments", {
        "slice": "listings", "in_response_to": mid,
        "entries": [{"layer": "B", "title": "another", "derives_from": ["I-01"]}],
    })
    assert status == 200 and "B-02" in str(out)
    assert ids(store, "m") == before


def test_a_fork_starts_with_no_issues_and_leaves_the_sources_alone(store, prompts):
    seed(store, prompts)
    call(store, prompts, "POST", "/projects/m/issues",
         {"target": "S-01", "kind": "additive", "claim": "an earlier run's finding"})
    _, out = fork(store, prompts)
    assert out["left_behind"]["open_issues"] == 1
    _, listed = call(store, prompts, "GET", "/projects/m-run1/issues")
    assert "earlier run" not in str(listed)
    _, listed = call(store, prompts, "GET", "/projects/m/issues")
    assert "earlier run" in str(listed)


def test_an_issue_raised_in_a_fork_can_be_repaired_there(store, prompts):
    seed(store, prompts)
    fork(store, prompts)
    _, raised = call(store, prompts, "POST", "/projects/m-run1/issues",
                     {"target": "S-01", "kind": "additive", "claim": "fault"})
    status, out = call(store, prompts, "POST", "/projects/m-run1/amendments", {
        "slice": "listings", "in_response_to": raised["id"],
        "entries": [{"layer": "S", "title": "fixed", "supersedes": "S-01",
                     "derives_from": ["A-01"]}],
    })
    assert status == 200, out


def test_a_fork_carries_no_delivery_state_and_cannot_ship_to_the_sources_repo(store, prompts):
    seed(store, prompts)
    store.set_repo("m", "owner/real")
    store.set_labels("m", ["team"], ["picked"])
    store.set_delegation("m", {"enabled": True})
    project_dir = store.units_path("m").parent
    for name in ("units.jsonl", "emissions.jsonl", "idempotency.jsonl"):
        (project_dir / name).write_text('{"key": "x"}\n', encoding="utf-8")
    fork(store, prompts)
    forked = store.load_manifest("m-run1")
    assert forked.repo is None and forked.delegation is None
    assert forked.labels == ("team",) and forked.pickup == ("picked",)
    for name in ("units.jsonl", "emissions.jsonl", "idempotency.jsonl"):
        assert not (store.units_path("m-run1").parent / name).exists()
    assert store.load_manifest("m").repo == "owner/real"


def test_a_fork_records_where_it_came_from(store, prompts):
    seed(store, prompts)
    fork(store, prompts)
    _, out = call(store, prompts, "GET", "/projects/m-run1/events?kind=forked")
    assert [e["facts"]["source"] for e in out["events"]] == ["m"]


def test_a_fork_can_be_forked(store, prompts):
    seed(store, prompts)
    fork(store, prompts, name="m-planted")
    status, _ = fork(store, prompts, name="m-planted-run1", source="m-planted")
    assert status == 201


def test_a_fork_needs_an_initiate_request(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "correction", "not this", project="m")
    status, _ = fork(store, prompts, mid=msg["message_id"])
    assert status == 400
    assert "m-run1" not in store.list_projects()


def test_a_fork_never_lands_on_an_existing_project(store, prompts):
    seed(store, prompts)
    assert fork(store, prompts)[0] == 201
    assert fork(store, prompts)[0] == 409


def test_an_unknown_source_is_404(store, prompts):
    seed(store, prompts)
    assert fork(store, prompts, source="nope")[0] == 404


@pytest.mark.parametrize("name", ["../escape", "a/b", "a.b", "", None])
def test_a_fork_name_must_be_a_routable_project_name(store, prompts, name):
    mid = seed(store, prompts)
    status, _ = fork(store, prompts, name=name, mid=mid)
    assert status == 400
    assert store.list_projects() == ["m"]
