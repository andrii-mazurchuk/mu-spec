"""Turning a cut into issues: two passes, and what the run reports.

Everything injected -- the client, and the function that fetches a unit -- so
the whole loop is exercised without a socket.

Two passes rather than one, because a dependency needs the blocker's issue id
and that does not exist until the blocker has been created. Creating in
dependency order would not help: the FIRST unit created still has nothing to
point at.
"""

from __future__ import annotations

import pytest

from mu_spec.emit import emit, read_emissions
from mu_spec.github import GitHubError, Issue

REPO = "andrii-mazurchuk/dark"

# S·01 follows its own test unit; S·02 follows S·01. So the order is
# S·01:T, S·01, S·02 and the wiring is two edges.
ORDER = ["S·01:T", "S·01", "S·02"]
EDGES = {"S·01": ("S·01:T",), "S·02": ("S·01",)}


def payload(key: str, follows=()) -> dict:
    return {
        "issued": True,
        "unit": {"key": key, "anchor": key.removesuffix(":T"),
                 "entries": [key.removesuffix(":T")], "modules": [f"{key}.py"],
                 "slices": ["core"], "size": {}},
        "write_set": [f"{key}.py"],
        "entries": [{"id": key.removesuffix(":T"), "title": f"{key} title",
                     "body": "the contract"}],
        "justification": [],
        "read_set": [], "cross_cutting": [], "file_scope": {},
        "follows": list(follows), "followed_by": [], "overlap": [],
        "tests": [], "tests_pending": [],
        "audit": {"editable_paths": [f"{key}.py"], "rule": "stay inside"},
    }


class FakeClient:
    """Records calls; hands out ascending numbers and ids."""

    def __init__(self, fail_on=(), fail_wiring=()) -> None:
        self.created: list[tuple] = []
        self.wired: list[tuple] = []
        self._n = 0
        self._fail_on = set(fail_on)
        self._fail_wiring = set(fail_wiring)

    def create_issue(self, repo, title, body, labels=()):
        if title.split(" ")[0] in self._fail_on:
            raise GitHubError("GitHub answered 422: Validation Failed", status=422)
        self._n += 1
        self.created.append((repo, title, body, tuple(labels)))
        return Issue(id=1000 + self._n, number=self._n,
                     url=f"https://github.com/{repo}/issues/{self._n}")

    def add_blocked_by(self, repo, blocked_number, blocker_id):
        if (blocked_number, blocker_id) in self._fail_wiring:
            raise GitHubError("GitHub answered 403: no", status=403)
        self.wired.append((repo, blocked_number, blocker_id))


def run(tmp_path, *, order=None, edges=None, client=None, **kw):
    client = client or FakeClient()
    log = tmp_path / "emissions.jsonl"
    result = emit(
        log_path=log,
        repo=REPO,
        cut_seq=2,
        order=order if order is not None else ORDER,
        edges=EDGES if edges is None else edges,
        fetch=lambda key: payload(key, EDGES.get(key, ())),
        client=client,
        now_fn=lambda: 1000.0,
        **kw,
    )
    return result, client, log


# -- the happy path ----------------------------------------------------------


def test_every_unit_becomes_an_issue_and_every_edge_becomes_a_dependency(tmp_path):
    result, client, _ = run(tmp_path)
    assert result["emitted"] is True
    assert [c["key"] for c in result["created"]] == ORDER
    assert len(client.created) == 3
    assert len(client.wired) == 2


def test_issues_are_created_in_dependency_order(tmp_path):
    """Not needed for correctness -- the second pass wires whatever exists --
    but issue numbers ascend, so a person scanning the list sees the test unit
    above the work it precedes instead of interleaved noise."""
    _, client, _ = run(tmp_path)
    assert [title.split(" ")[0] for _, title, _, _ in client.created] == ORDER


def test_a_dependency_is_wired_with_the_blockers_id_and_the_blocked_number(tmp_path):
    """The trap the client exists to avoid, checked end to end: S·01 is issue
    number 2 and is blocked by S·01:T, which is issue number 1 with id 1001."""
    _, client, _ = run(tmp_path)
    assert (REPO, 2, 1001) in client.wired


def test_the_result_names_every_issue_it_made(tmp_path):
    """The observability half. A run of 144 units has to be readable as a
    pass/fail list, not inferred from a count."""
    result, _, _ = run(tmp_path)
    first = result["created"][0]
    assert first["key"] == "S·01:T"
    assert first["number"] == 1
    assert first["url"].endswith("/issues/1")
    assert first["title"]
    assert result["counts"] == {
        "units": 3, "created": 3, "skipped": 0, "failed": 0,
        "wired": 2, "unwired": 0,
    }


# -- the log, and not doing it twice -----------------------------------------


def test_the_emission_is_recorded_so_the_issues_can_be_found_again(tmp_path):
    _, _, log = run(tmp_path)
    stored = read_emissions(log)
    assert len(stored) == 1
    assert stored[0].cut_seq == 2
    assert stored[0].repo == REPO
    assert stored[0].issues["S·01"]["number"] == 2


def test_running_again_creates_nothing_and_says_why(tmp_path):
    """The guard that matters. A second run of 144 units must not produce a
    second 144 issues -- and refusing the whole cut instead would leave a
    partial run permanently unfinishable, so it skips per unit."""
    run(tmp_path)
    result, client, _ = run(tmp_path)
    assert client.created == []
    assert client.wired == []
    assert [s["key"] for s in result["skipped"]] == ORDER
    assert "already" in result["skipped"][0]["reason"]
    assert result["counts"]["created"] == 0


def test_a_partial_run_is_finished_by_running_again(tmp_path):
    """Pass one fails for S·02 only. The re-run creates S·02 and nothing else,
    and wires the edge that could not be wired the first time."""
    first, client, log = run(tmp_path, client=FakeClient(fail_on={"S·02"}))
    assert [f["key"] for f in first["failed"]] == ["S·02"]
    assert len(client.created) == 2

    second, client2, _ = run(tmp_path)
    assert [c["key"] for c in second["created"]] == ["S·02"]
    assert [s["key"] for s in second["skipped"]] == ["S·01:T", "S·01"]
    assert (REPO, 1, 1002) in client2.wired, "S·02 blocked by S·01"


def test_a_dependency_already_wired_is_not_wired_twice(tmp_path):
    run(tmp_path)
    result, client, _ = run(tmp_path)
    assert client.wired == []
    assert result["counts"]["wired"] == 0


# -- refusing, and degrading -------------------------------------------------


def test_nothing_to_emit_is_not_an_error(tmp_path):
    result, client, log = run(tmp_path, order=[], edges={})
    assert result["emitted"] is False
    assert "no work units" in result["reason"]
    assert client.created == []
    assert read_emissions(log) == (), "nothing happened, so nothing is recorded"


def test_a_unit_that_cannot_be_rendered_is_reported_and_the_rest_continue(tmp_path):
    """An unsound graph refuses `get_work_unit` per unit. One bad unit must not
    cost the other hundred and forty-three."""
    def fetch(key):
        if key == "S·01":
            return {"issued": False, "reason": "graph is unsound"}
        return payload(key, EDGES.get(key, ()))

    client = FakeClient()
    result = emit(
        log_path=tmp_path / "e.jsonl", repo=REPO, cut_seq=2, order=ORDER,
        edges=EDGES, fetch=fetch, client=client, now_fn=lambda: 1.0,
    )
    assert [f["key"] for f in result["failed"]] == ["S·01"]
    assert "unsound" in result["failed"][0]["reason"]
    assert len(result["created"]) == 2


def test_an_oversized_ticket_is_skipped_rather_than_sent_to_be_refused(tmp_path):
    """44.7 KB is the largest real body against a 65,536 ceiling. A body over
    it takes a 422 that names nothing useful, so it is caught here where the
    reason can be stated."""
    from mu_spec.render import BODY_LIMIT

    def fetch(key):
        p = payload(key, EDGES.get(key, ()))
        if key == "S·02":
            p["entries"][0]["body"] = "x" * (BODY_LIMIT + 10)
        return p

    client = FakeClient()
    result = emit(
        log_path=tmp_path / "e.jsonl", repo=REPO, cut_seq=2, order=ORDER,
        edges=EDGES, fetch=fetch, client=client, now_fn=lambda: 1.0,
    )
    assert [s["key"] for s in result["skipped"]] == ["S·02"]
    assert "too large" in result["skipped"][0]["reason"]
    assert len(client.created) == 2


def test_an_edge_whose_blocker_never_got_created_is_reported_unwired(tmp_path):
    """Order is the point of the whole feature, so an order that silently did
    not happen is the worst available outcome."""
    result, _, _ = run(tmp_path, client=FakeClient(fail_on={"S·01:T"}))
    assert [u["blocked"] for u in result["unwired"]] == ["S·01"]
    assert "S·01:T" in result["unwired"][0]["reason"]


def test_a_failed_wiring_call_is_reported_and_the_rest_continue(tmp_path):
    result, client, _ = run(tmp_path, client=FakeClient(fail_wiring={(2, 1001)}))
    assert len(result["created"]) == 3
    assert [u["blocked"] for u in result["unwired"]] == ["S·01"]
    assert result["counts"]["wired"] == 1


def test_what_was_created_is_recorded_even_when_the_run_went_badly(tmp_path):
    """Otherwise a re-run duplicates the issues that DID land, which is the one
    thing a duplicate guard exists to stop."""
    _, _, log = run(tmp_path, client=FakeClient(fail_on={"S·02"}))
    stored = read_emissions(log)
    assert set(stored[0].issues) == {"S·01:T", "S·01"}


def test_an_unreadable_log_is_a_hard_error_not_an_empty_one(tmp_path):
    """Degrading to "nothing has been emitted" would create every issue a
    second time. This is the one place in the feature where absence must not
    be treated as normal."""
    log = tmp_path / "emissions.jsonl"
    log.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_emissions(log)


# -- the service wrapper: config, the cut, and refusing cleanly --------------


def _project(tmp_path):
    """A sound project with one spec entry, one scenario, and both implemented."""
    from mu_spec.graph import Entry
    from mu_spec.identifiers import parse
    from mu_spec.storage import ProjectStore

    store = ProjectStore(tmp_path)
    store.create_project("p")
    store.append("p", [Entry(id=parse("I·01"), title="intent")])
    store.append("p", [Entry(id=parse("B·01"), derives_from=(parse("I·01"),),
                             title="behaviour")], slice_name="core")
    store.append("p", [Entry(id=parse("A·01"), derives_from=(parse("B·01"),),
                             title="arch")], slice_name="core")
    store.append("p", [Entry(id=parse("S·01"), derives_from=(parse("A·01"),),
                             title="the contract", body="build it")],
                 slice_name="core")
    store.append("p", [Entry(id=parse("T·01"), derives_from=(parse("S·01"),),
                             title="the case", purpose="why")])
    store.set_module("p", "app/thing.py", ["S·01"])
    store.set_module("p", "tests/test_thing.py", ["T·01"])
    return store


def _cut(store):
    from mu_spec import service
    return service.cut_units(store, "p", {"note": "ready"})


def test_emitting_without_a_repo_refuses_and_says_so(tmp_path, monkeypatch):
    from mu_spec import service

    store = _project(tmp_path)
    _cut(store)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    result = service.emit_tickets(store, "p", client=FakeClient())
    assert result["emitted"] is False
    assert "repo" in result["reason"]


def test_emitting_without_a_token_refuses_and_names_the_variable(tmp_path, monkeypatch):
    """The token is unit config, not project metadata, so the reason has to say
    which variable is missing -- there is nothing in the project to inspect."""
    from mu_spec import service

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    _cut(store)
    monkeypatch.delenv("MU_SPEC_GITHUB_TOKEN", raising=False)
    result = service.emit_tickets(store, "p")
    assert result["emitted"] is False
    assert "MU_SPEC_GITHUB_TOKEN" in result["reason"]


def test_emitting_before_a_cut_refuses(tmp_path, monkeypatch):
    """A cut is the deliberate decision that work goes out from this shape.
    Without one there is nothing anybody chose to emit."""
    from mu_spec import service

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    result = service.emit_tickets(store, "p", client=FakeClient())
    assert result["emitted"] is False
    assert "cut" in result["reason"]


def test_emitting_a_cut_the_graph_has_moved_past_refuses(tmp_path, monkeypatch):
    """Bodies come from the live graph, and the unit list comes from the cut.
    If those disagree, emission would ship a shape nobody cut -- so drift is a
    refusal with the drift attached, not a silent preference for one side."""
    from mu_spec import service
    from mu_spec.graph import Entry
    from mu_spec.identifiers import parse

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    _cut(store)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")

    store.append("p", [Entry(id=parse("S·02"), derives_from=(parse("A·01"),),
                             title="another")], slice_name="core")
    store.set_module("p", "app/other.py", ["S·02"])

    result = service.emit_tickets(store, "p", client=FakeClient())
    assert result["emitted"] is False
    assert "moved since cut" in result["reason"]
    assert result["drift"]["changed"], "the drift itself comes back attached"


def test_a_sound_project_emits_its_units_with_the_test_unit_first(tmp_path, monkeypatch):
    from mu_spec import service

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    _cut(store)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")

    client = FakeClient()
    result = service.emit_tickets(store, "p", client=client)
    assert result["emitted"] is True
    assert [c["key"] for c in result["created"]] == ["S·01:T", "S·01"]
    # And the order was declared: S·01 is blocked by its scenarios.
    assert result["counts"]["wired"] == 1
    assert result["wired"][0] == {
        "blocked": "S·01", "blocker": "S·01:T",
        "blocked_number": 2, "blocker_id": 1001,
    }


def test_the_run_is_recorded_against_the_project(tmp_path, monkeypatch):
    from mu_spec import service

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    _cut(store)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    service.emit_tickets(store, "p", client=FakeClient())

    stored = read_emissions(store.emissions_path("p"))
    assert len(stored) == 1
    assert set(stored[0].issues) == {"S·01", "S·01:T"}
