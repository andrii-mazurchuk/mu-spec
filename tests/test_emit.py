"""Turning a cut into issues: two passes, and what the run reports.

Everything injected -- the client, and the function that fetches a unit -- so
the whole loop is exercised without a socket.

Two passes rather than one, because a dependency needs the blocker's issue id
and that does not exist until the blocker has been created. Creating in
dependency order would not help: the FIRST unit created still has nothing to
point at.
"""

from __future__ import annotations

import json

import pytest

from mu_spec.emit import Runs, emit, read_emissions, read_log, rollback
from mu_spec.github import GitHubError, Issue

REPO = "andrii-mazurchuk/dark"

# S-01 follows its own test unit; S-02 follows S-01. So the order is
# S-01-T, S-01, S-02 and the wiring is two edges.
ORDER = ["S-01-T", "S-01", "S-02"]
EDGES = {"S-01": ("S-01-T",), "S-02": ("S-01",)}


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

    def __init__(self, fail_on=(), fail_wiring=(), start=0) -> None:
        self.created: list[tuple] = []
        self.wired: list[tuple] = []
        # `start` exists because GitHub never reuses an issue number. A second
        # client numbering from 1 hands replacement issues the numbers the
        # withdrawn ones had, which hides a real bug -- see the test named for
        # it below.
        self._n = start
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
    """The trap the client exists to avoid, checked end to end: S-01 is issue
    number 2 and is blocked by S-01-T, which is issue number 1 with id 1001."""
    _, client, _ = run(tmp_path)
    assert (REPO, 2, 1001) in client.wired


def test_the_result_names_every_issue_it_made(tmp_path):
    """The observability half. A run of 144 units has to be readable as a
    pass/fail list, not inferred from a count."""
    result, _, _ = run(tmp_path)
    first = result["created"][0]
    assert first["key"] == "S-01-T"
    assert first["number"] == 1
    assert first["url"].endswith("/issues/1")
    assert first["title"]
    assert result["counts"] == {
        "units": 3, "created": 3, "skipped": 0, "failed": 0,
        "wired": 2, "unwired": 0, "edited": 0, "withdrawn": 0,
        "touched": 0, "raced": 0, "held": 0, "removed": 0,
    }


# -- the log, and not doing it twice -----------------------------------------


def test_the_emission_is_recorded_so_the_issues_can_be_found_again(tmp_path):
    _, _, log = run(tmp_path)
    stored = read_emissions(log)
    assert len(stored) == 1
    assert stored[0].cut_seq == 2
    assert stored[0].repo == REPO
    assert stored[0].issues["S-01"]["number"] == 2


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
    """Pass one fails for S-02 only. The re-run creates S-02 and nothing else,
    and wires the edge that could not be wired the first time."""
    first, client, log = run(tmp_path, client=FakeClient(fail_on={"S-02"}))
    assert [f["key"] for f in first["failed"]] == ["S-02"]
    assert len(client.created) == 2

    second, client2, _ = run(tmp_path)
    assert [c["key"] for c in second["created"]] == ["S-02"]
    assert [s["key"] for s in second["skipped"]] == ["S-01-T", "S-01"]
    assert (REPO, 1, 1002) in client2.wired, "S-02 blocked by S-01"


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
        if key == "S-01":
            return {"issued": False, "reason": "graph is unsound"}
        return payload(key, EDGES.get(key, ()))

    client = FakeClient()
    result = emit(
        log_path=tmp_path / "e.jsonl", repo=REPO, cut_seq=2, order=ORDER,
        edges=EDGES, fetch=fetch, client=client, now_fn=lambda: 1.0,
    )
    assert [f["key"] for f in result["failed"]] == ["S-01"]
    assert "unsound" in result["failed"][0]["reason"]
    assert len(result["created"]) == 2


def test_an_oversized_ticket_is_skipped_rather_than_sent_to_be_refused(tmp_path):
    """44.7 KB is the largest real body against a 65,536 ceiling. A body over
    it takes a 422 that names nothing useful, so it is caught here where the
    reason can be stated."""
    from mu_spec.render import BODY_LIMIT

    def fetch(key):
        p = payload(key, EDGES.get(key, ()))
        if key == "S-02":
            p["entries"][0]["body"] = "x" * (BODY_LIMIT + 10)
        return p

    client = FakeClient()
    result = emit(
        log_path=tmp_path / "e.jsonl", repo=REPO, cut_seq=2, order=ORDER,
        edges=EDGES, fetch=fetch, client=client, now_fn=lambda: 1.0,
    )
    assert [s["key"] for s in result["skipped"]] == ["S-02"]
    assert "too large" in result["skipped"][0]["reason"]
    assert len(client.created) == 2


def test_an_edge_whose_blocker_never_got_created_is_reported_unwired(tmp_path):
    """Order is the point of the whole feature, so an order that silently did
    not happen is the worst available outcome."""
    result, _, _ = run(tmp_path, client=FakeClient(fail_on={"S-01-T"}))
    assert [u["blocked"] for u in result["unwired"]] == ["S-01"]
    assert "S-01-T" in result["unwired"][0]["reason"]


def test_a_failed_wiring_call_is_reported_and_the_rest_continue(tmp_path):
    result, client, _ = run(tmp_path, client=FakeClient(fail_wiring={(2, 1001)}))
    assert len(result["created"]) == 3
    assert [u["blocked"] for u in result["unwired"]] == ["S-01"]
    assert result["counts"]["wired"] == 1


def test_what_was_created_is_recorded_even_when_the_run_went_badly(tmp_path):
    """Otherwise a re-run duplicates the issues that DID land, which is the one
    thing a duplicate guard exists to stop."""
    _, _, log = run(tmp_path, client=FakeClient(fail_on={"S-02"}))
    stored = read_emissions(log)
    assert set(stored[0].issues) == {"S-01-T", "S-01"}


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
    store.append("p", [Entry(id=parse("I-01"), title="intent")])
    store.append("p", [Entry(id=parse("B-01"), derives_from=(parse("I-01"),),
                             title="behaviour")], slice_name="core")
    store.append("p", [Entry(id=parse("A-01"), derives_from=(parse("B-01"),),
                             title="arch")], slice_name="core")
    store.append("p", [Entry(id=parse("S-01"), derives_from=(parse("A-01"),),
                             title="the contract", body="build it")],
                 slice_name="core")
    store.append("p", [Entry(id=parse("T-01"), derives_from=(parse("S-01"),),
                             title="the case", purpose="why")])
    store.set_module("p", "app/thing.py", ["S-01"])
    store.set_module("p", "tests/test_thing.py", ["T-01"])
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

    store.append("p", [Entry(id=parse("S-02"), derives_from=(parse("A-01"),),
                             title="another")], slice_name="core")
    store.set_module("p", "app/other.py", ["S-02"])

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
    assert [c["key"] for c in result["created"]] == ["S-01-T", "S-01"]
    # And the order was declared: S-01 is blocked by its scenarios.
    assert result["counts"]["wired"] == 1
    assert result["wired"][0] == {
        "blocked": "S-01", "blocker": "S-01-T",
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
    assert set(stored[0].issues) == {"S-01", "S-01-T"}


# -- tracking a run in flight -----------------------------------------------


def _runs(inline=True):
    from mu_spec.emit import Runs
    # `spawn` injected so the suite is deterministic and thread-free: inline
    # means the "background" work finishes before start() returns, which is
    # exactly what a test wants and never what production wants.
    return Runs(spawn=(lambda fn: fn()) if inline else None)


def test_a_run_reports_its_progress_while_it_goes(tmp_path):
    seen = []
    log = tmp_path / "e.jsonl"
    emit(
        log_path=log, repo=REPO, cut_seq=2, order=ORDER, edges=EDGES,
        fetch=lambda k: payload(k, EDGES.get(k, ())), client=FakeClient(),
        now_fn=lambda: 1.0, on_progress=seen.append,
    )
    # One after each unit and each edge, so 3 + 2.
    assert len(seen) == 5
    assert seen[0]["step"] == "creating"
    assert seen[-1]["step"] == "wiring"
    assert [s["processed"] for s in seen[:3]] == [1, 2, 3]
    assert seen[2]["total"] == 3


def test_starting_a_run_returns_something_trackable(tmp_path):
    runs = _runs()
    run = runs.start("dark", lambda report: {"emitted": True, "counts": {}})
    assert run.id
    status = runs.status("dark")
    assert status["project"] == "dark"
    assert status["phase"] == "done"
    assert status["finished_at"] is not None


def test_two_runs_for_one_project_are_refused_rather_than_interleaved(tmp_path):
    """Careful implementation: two concurrent emissions would each read the log
    before the other wrote it, and both would create every issue."""
    from mu_spec.emit import RunBusy

    # A spawn that never runs the work, so the first run stays "running".
    runs = _runs(inline=False)
    runs._spawn = lambda fn: None  # noqa: SLF001 -- the point of the test
    runs.start("dark", lambda report: None)
    with pytest.raises(RunBusy):
        runs.start("dark", lambda report: None)


def test_a_finished_run_does_not_block_the_next_one(tmp_path):
    runs = _runs()
    first = runs.start("dark", lambda report: {"emitted": True, "counts": {}})
    second = runs.start("dark", lambda report: {"emitted": True, "counts": {}})
    assert first.id != second.id


def test_two_projects_run_independently(tmp_path):
    runs = _runs(inline=False)
    runs._spawn = lambda fn: None  # noqa: SLF001
    runs.start("dark", lambda report: None)
    runs.start("t-finance", lambda report: None)
    assert runs.status("dark")["phase"] == "running"
    assert runs.status("t-finance")["phase"] == "running"


def test_a_run_that_raises_ends_failed_and_keeps_the_reason(tmp_path):
    """A thread that dies silently leaves a run "running" forever, and the
    dashboard shows a spinner nobody can clear."""
    runs = _runs()

    def boom(report):
        raise RuntimeError("the store went away")

    runs.start("dark", boom)
    status = runs.status("dark")
    assert status["phase"] == "failed"
    assert "the store went away" in status["error"]
    assert status["finished_at"] is not None


def test_a_refusal_is_a_finished_run_not_a_failed_one(tmp_path):
    """No repo, no cut, drift -- those are answers. Reporting them as failures
    would put an error on the dashboard for a project that is merely not ready."""
    runs = _runs()
    runs.start("dark", lambda report: {"emitted": False, "reason": "no cut"})
    status = runs.status("dark")
    assert status["phase"] == "done"
    assert status["error"] is None
    assert status["result"]["reason"] == "no cut"


def test_progress_reaches_the_status_as_the_work_proceeds(tmp_path):
    runs = _runs()

    def work(report):
        report({"step": "creating", "total": 10, "processed": 4, "created": 4})
        return {"emitted": True, "counts": {"created": 4}}

    runs.start("dark", work)
    status = runs.status("dark")
    assert (status["total"], status["processed"], status["created"]) == (10, 4, 4)


def test_progress_keeps_advancing_report_after_report(tmp_path):
    """The regression. `phase` once meant both the lifecycle and the work step,
    so the first report set it to "creating", every later report failed the
    "still running?" guard, and a six-minute run showed processed=1 throughout.

    Caught by driving the route, not by the suite: one test sent a single
    report and the other read the callback rather than the run."""
    runs = _runs()

    def work(report):
        for n in (1, 2, 3, 4):
            report({"step": "creating", "total": 4, "processed": n})
        return {"emitted": True, "counts": {}}

    runs.start("dark", work)
    status = runs.status("dark")
    assert status["processed"] == 4, "progress stopped being reported"
    assert status["phase"] == "done", "the worker must not set the lifecycle"


def test_a_worker_cannot_overwrite_the_runs_lifecycle(tmp_path):
    """`phase` belongs to the run. A worker reporting `phase: done` would make
    a live run look finished and let a second one start beside it."""
    runs = _runs(inline=False)
    runs._spawn = lambda fn: fn()  # noqa: SLF001

    def work(report):
        report({"phase": "done", "step": "creating", "processed": 1})
        return {"emitted": True, "counts": {}}

    run = runs.start("dark", work)
    assert run.snapshot()["phase"] == "done"  # set by finish, not the worker


def test_status_for_a_project_that_never_ran_is_absent_not_an_error(tmp_path):
    assert _runs().status("nothing-here") is None


def test_a_real_thread_finishes_and_is_observable(tmp_path):
    """One test that actually threads, because `spawn` being injectable is only
    worth anything if the default it replaces works."""
    import time as _time

    from mu_spec.emit import Runs

    runs = Runs()
    done = []
    runs.start("dark", lambda report: done.append(1) or {"emitted": True})
    for _ in range(200):
        if runs.status("dark")["phase"] in ("done", "failed"):
            break
        _time.sleep(0.01)
    assert runs.status("dark")["phase"] == "done"
    assert done == [1]


def test_the_emission_status_says_which_units_are_already_out(tmp_path, monkeypatch):
    """What a reader needs BEFORE pressing: how many issues this would newly
    create. A count of work units is not that -- a re-run of a 144-unit cut
    creates none of them -- and the difference is the whole question.

    Scoped to the current cut and repo, exactly as the skip is: a unit emitted
    under an older cut is not skipped, so reporting it as already out would
    promise a smaller run than the one that happens."""
    from mu_spec import service

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    _cut(store)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")

    before = service.get_emission(store, "p")
    assert before["already"] == {}
    assert before["cut_seq"] == 1

    service.emit_tickets(store, "p", client=FakeClient())
    after = service.get_emission(store, "p")
    assert set(after["already"]) == {"S-01", "S-01-T"}
    assert after["already"]["S-01"]["number"] == 2


def test_units_emitted_under_an_older_cut_are_still_out(tmp_path, monkeypatch):
    """Identity is per repository, never per cut (docs/TICKETS.md section 1).
    Reporting them as not out is how a new cut used to promise, and then
    perform, a second copy of every issue."""
    from mu_spec import service
    from mu_spec.graph import Entry
    from mu_spec.identifiers import parse

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    _cut(store)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    service.emit_tickets(store, "p", client=FakeClient())

    # The graph moves, and a fresh cut is taken.
    store.append("p", [Entry(id=parse("S-02"), derives_from=(parse("A-01"),),
                             title="another")], slice_name="core")
    store.set_module("p", "app/other.py", ["S-02"])
    _cut(store)

    status = service.get_emission(store, "p")
    assert status["cut_seq"] == 2
    assert set(status["already"]) == {"S-01", "S-01-T"}


def test_the_status_says_whether_a_token_exists_never_what_it_is(tmp_path, monkeypatch):
    """The one precondition the panel could not check, and the one most likely
    to be wrong on a fresh deploy: the token lives in this unit's environment,
    so nothing in the project says whether it is there.

    Presence only. The value never leaves the process -- a secret on a page
    that is read, screenshotted and rendered in somebody's browser has leaked
    whatever it was protecting."""
    from mu_spec import service

    store = _project(tmp_path)

    monkeypatch.delenv("MU_SPEC_GITHUB_TOKEN", raising=False)
    assert service.get_emission(store, "p")["token"] is False

    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "ghp_a_real_looking_secret")
    status = service.get_emission(store, "p")
    assert status["token"] is True
    assert "ghp_a_real_looking_secret" not in json.dumps(status)


def test_a_blank_token_counts_as_absent(tmp_path, monkeypatch):
    """An env var set to empty string is how a token most often goes missing --
    a substitution that resolved to nothing. Reporting it as present would
    make the chip green and the run refuse anyway."""
    from mu_spec import service

    store = _project(tmp_path)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "   ")
    assert service.get_emission(store, "p")["token"] is False


# -- withdrawing a batch -----------------------------------------------------


def _closer(client, states=None):
    """Teach the FakeClient to close, recording the order it was asked.

    `states` maps issue number -> what a read reports. Anything not named is
    open, which is the ordinary case.
    """
    client.closed = []
    client.states = dict(states or {})

    def issue_state(repo, number):
        return client.states.get(
            number, {"state": "open", "state_reason": ""}
        )

    client.issue_state = issue_state

    def close_issue(repo, number, reason="not_planned"):
        if number in getattr(client, "fail_close", ()):
            raise GitHubError("GitHub answered 403: no", status=403)
        client.closed.append((repo, number, reason))

    client.close_issue = close_issue
    return client


def _rollback(log, client, **kw):
    return rollback(log_path=log, repo=REPO, cut_seq=2, client=client,
                    now_fn=lambda: 2000.0, **kw)


def test_a_rollback_closes_every_issue_of_the_cut(tmp_path):
    _result, client, log = run(tmp_path)
    _closer(client)
    out = _rollback(log, client)
    assert out["rolled_back"] is True
    assert out["counts"] == {"targeted": 3, "closed": 3, "failed": 0,
                             "skipped": 0}
    assert {n for _r, n, _why in client.closed} == {1, 2, 3}
    assert all(why == "not_planned" for _r, _n, why in client.closed), (
        "withdrawn is not completed"
    )


def test_a_rollback_closes_the_dependent_before_what_it_depends_on(tmp_path):
    """An emission creates a test unit before the implementation that follows
    it, so withdrawing in reverse never leaves a live issue blocked by a
    withdrawn one, however far a failing run gets."""
    _result, client, log = run(tmp_path)
    _closer(client)
    _rollback(log, client)
    assert [n for _r, n, _w in client.closed] == [3, 2, 1]


def test_a_withdrawn_unit_is_emitted_again(tmp_path):
    """The whole point. Without subtracting the rollback, the duplicate guard
    skips every unit whose issue is now closed and the re-emission creates
    nothing at all."""
    _result, client, log = run(tmp_path)
    _closer(client)
    _rollback(log, client)
    again, client2, _ = run(tmp_path, client=_closer(FakeClient()))
    # Same log file: `run` builds the path from tmp_path.
    assert again["counts"]["created"] == 3, again["counts"]
    assert again["counts"]["skipped"] == 0
    assert again["counts"]["wired"] == 2, "the edges are declared again too"


def test_an_issue_that_would_not_close_is_not_recreated(tmp_path):
    """Left out of `closed` on purpose. An issue still open on the tracker
    must not be emitted a second time -- that turns one stray issue into two,
    which is worse than the thing being repaired."""
    _result, client, log = run(tmp_path)
    _closer(client)
    client.fail_close = {2}
    out = _rollback(log, client)
    assert out["counts"] == {"targeted": 3, "closed": 2, "failed": 1,
                             "skipped": 0}
    assert out["failed"][0]["number"] == 2
    again, _c, _l = run(tmp_path, client=FakeClient())
    assert again["counts"]["created"] == 2
    assert [s["key"] for s in again["skipped"]] == ["S-01"], (
        "the one still open on GitHub stays skipped"
    )


def test_a_rollback_is_appended_and_erases_nothing(tmp_path):
    """The log is the audit trail. What was created and then withdrawn is two
    facts, and a log keeping only the second could not say why #2 is closed."""
    _result, client, log = run(tmp_path)
    _closer(client)
    _rollback(log, client)
    emissions, rollbacks = read_log(log)
    assert len(emissions) == 1, "the emission is still there"
    assert len(rollbacks) == 1
    assert rollbacks[0].undone == (emissions[0].seq,)
    assert rollbacks[0].closed == {"S-01-T": 1, "S-01": 2, "S-02": 3}
    # One sequence across both kinds.
    assert rollbacks[0].seq == emissions[0].seq + 1


def test_rolling_back_twice_withdraws_nothing_the_second_time(tmp_path):
    _result, client, log = run(tmp_path)
    _closer(client)
    _rollback(log, client)
    client.closed.clear()
    out = _rollback(log, client)
    assert out["rolled_back"] is False
    assert "nothing to withdraw" in out["reason"]
    assert client.closed == []


def test_a_rollback_reports_progress_as_it_goes(tmp_path):
    """Same reason the emission does: 144 paced closes is minutes, and a
    spinner is not observability."""
    _result, client, log = run(tmp_path)
    _closer(client)
    seen = []
    _rollback(log, client, on_progress=seen.append)
    assert seen[0]["total"] == 3
    assert [s["processed"] for s in seen] == [0, 1, 2, 3]
    assert seen[-1]["closed"] == 3


def test_a_unit_emitted_again_can_be_withdrawn_again(tmp_path):
    """A withdrawal is of an ISSUE, not of a unit key.

    Tracking it by key made the second issue under a key permanently
    un-withdrawable: the first rollback had already claimed the name, so the
    second reported success and closed nothing. Found by driving two full
    rounds against dark, and invisible to a test whose second client numbers
    issues from 1 again -- because then the replacements reuse the withdrawn
    numbers, which GitHub never does.
    """
    _result, client, log = run(tmp_path)
    _closer(client)
    _rollback(log, client)

    second = _closer(FakeClient(start=client._n))
    run(tmp_path, client=second)
    out = _rollback(log, second)
    assert out["rolled_back"] is True
    assert out["counts"] == {"targeted": 3, "closed": 3, "failed": 0,
                             "skipped": 0}
    assert {n for _r, n, _w in second.closed} == {4, 5, 6}, (
        "the replacements, not the issues already withdrawn"
    )


def test_a_withdrawal_never_overwrites_someone_elses_close(tmp_path):
    """`not_planned` is this unit's word and means withdrawn. `completed` is
    the consumer's and means DONE.

    This happened for real on 2026-09-29: a consumer finished one unit of a
    144-issue batch and closed its issue `completed`; a withdrawal of the
    batch relabelled it `not_planned`, which reads as "withdrawn, may be
    re-admitted" -- so finished work would have been handed back to an agent,
    on a branch that already existed.
    """
    _result, client, log = run(tmp_path)
    _closer(client, states={2: {"state": "closed", "state_reason": "completed"}})
    out = _rollback(log, client)
    assert [n for _r, n, _w in client.closed] == [3, 1], "issue 2 untouched"
    assert out["counts"] == {"targeted": 3, "closed": 2, "failed": 0,
                             "skipped": 1}
    assert out["skipped"][0]["number"] == 2
    assert "completed" in out["skipped"][0]["reason"]


def test_work_somebody_finished_is_not_recreated(tmp_path):
    """The half that matters more. A skipped issue is NOT recorded as
    withdrawn, so the duplicate guard still considers that unit emitted --
    re-emitting it would ask for work that is already merged."""
    _result, client, log = run(tmp_path)
    _closer(client, states={2: {"state": "closed", "state_reason": "completed"}})
    _rollback(log, client)
    again, _c, _l = run(tmp_path, client=_closer(FakeClient(start=9)))
    assert [s["key"] for s in again["skipped"]] == ["S-01"], (
        "the finished unit stays out"
    )
    assert "S-01" not in [c["key"] for c in again["created"]]


def test_an_issue_this_unit_already_withdrew_is_closed_again_harmlessly(tmp_path):
    """Our own marker is the one reason we may overwrite: a re-run of a
    partial withdrawal has to be able to finish the job."""
    _result, client, log = run(tmp_path)
    _closer(client,
            states={2: {"state": "closed", "state_reason": "not_planned"}})
    out = _rollback(log, client)
    assert out["counts"]["closed"] == 3
    assert out["counts"]["skipped"] == 0


def test_an_unreadable_state_leaves_the_issue_alone(tmp_path):
    """Not knowing is a reason to stop, not to proceed: a skipped issue can be
    withdrawn on a re-run, a clobbered one cannot be restored."""
    _result, client, log = run(tmp_path)
    _closer(client)

    def broken(repo, number):
        raise GitHubError("GitHub answered 502", status=502)

    client.issue_state = broken
    out = _rollback(log, client)
    assert client.closed == [], "nothing was touched"
    assert out["counts"] == {"targeted": 3, "closed": 0, "failed": 0,
                             "skipped": 3}
    assert "could not read" in out["skipped"][0]["reason"]


def test_a_rollback_reports_what_it_has_closed(tmp_path):
    """`closed` has to be in the run's field list or the page shows a bar
    moving and a count of zero beside it, for the whole withdrawal."""
    _result, client, log = run(tmp_path)
    _closer(client)
    runs = Runs(spawn=lambda fn: fn())
    runs.start("m", lambda report: rollback(
        log_path=log, repo=REPO, cut_seq=2, client=client,
        now_fn=lambda: 2000.0, on_progress=report,
    ))
    snap = runs.status("m")
    assert snap["result"]["counts"]["closed"] == 3
    # And mid-run, which is what the page actually reads.
    seen = []
    _closer(client)
    rollback(log_path=log, repo=REPO, cut_seq=2, client=client,
             now_fn=lambda: 2001.0, on_progress=seen.append)
    assert all("closed" in s for s in seen) or not seen


def test_a_superseded_anchor_keeps_its_issue_in_the_status_and_the_ship(tmp_path, monkeypatch):
    """Superseding S-01 with S-02 renames its units, S-01 -> S-02 and S-01-T
    -> S-02-T. Both are the same work, already out, so neither the pre-flight
    count nor the press may treat them as new. DARK's S-107 -> S-127."""
    from mu_spec import service
    from mu_spec.graph import Entry
    from mu_spec.identifiers import parse

    store = _project(tmp_path)
    store.set_repo("p", "owner/name")
    _cut(store)
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    service.emit_tickets(store, "p", client=FakeClient())

    store.append("p", [Entry(id=parse("S-02"), derives_from=(parse("A-01"),),
                             title="the contract, corrected", body="build it",
                             supersedes=parse("S-01"))], slice_name="core")
    store.append("p", [Entry(id=parse("T-02"), derives_from=(parse("S-02"),),
                             title="the case", purpose="why",
                             supersedes=parse("T-01"))])
    store.set_module("p", "app/thing.py", ["S-02"])
    store.set_module("p", "tests/test_thing.py", ["T-02"])
    _cut(store)

    status = service.get_emission(store, "p")
    assert {k: v["number"] for k, v in status["already"].items()} == {
        "S-02-T": 1, "S-02": 2}

    client = FakeClient(start=2)
    result = service.emit_tickets(store, "p", client=client)
    assert result["emitted"] is True
    assert result["created"] == [] and client.created == []
    assert result["withdrawn"] == []
    assert sorted(t["key"] for t in result["touched"]) == ["S-02", "S-02-T"]
