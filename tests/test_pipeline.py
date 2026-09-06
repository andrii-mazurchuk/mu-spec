from __future__ import annotations

import pytest

from mu_spec import service
from mu_spec.dispatch import DERIVATION, TRIAGE, Dispatch
from mu_spec.inbox import Inbox
from mu_spec.issues import IssueLog
from mu_spec.lifecycle import SESSION, Lifecycle
from mu_spec.runner import SessionResult, gate
from mu_spec.storage import ProjectStore


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def parts(store):
    return (
        store,
        Inbox(store.inbox_path()),
        IssueLog(store.issues_path()),
        Lifecycle(store.events_path()),
    )


class _Runner:
    """Replaces the real launcher. Records what it was asked to run."""

    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, dispatch, brief):
        self.calls.append((dispatch, brief))
        return SessionResult(dispatch.session_type, dispatch.project, self.ok, 1.5, "")


def _seed_request(store, inbox, project="m"):
    """One pending request against an empty project -- the simplest state
    that has something eligible in it."""
    store.create_project(project)
    service.post_to_inbox(
        inbox,
        {"type": "feature", "title": "search is missing", "project": project},
        now_fn=lambda: 1.0,
    )


def _run(parts, runner, tmp_path, **kw):
    store, inbox, issues, events = parts
    return service.run_pipeline(
        store,
        inbox,
        issues,
        events,
        session_root=tmp_path / "session_types",
        base_url="http://127.0.0.1:9006",
        runner=runner,
        now_fn=lambda: 99.0,
        **kw,
    )


# -- the floor ---------------------------------------------------------------


def test_nothing_eligible_runs_nothing(parts, tmp_path):
    """No project, no request, nothing unserved. The loop must not launch a
    session whose only finding is that there was no work."""
    runner = _Runner()
    out = _run(parts, runner, tmp_path)
    assert out["ran"] is False
    assert runner.calls == []


def test_a_held_lock_runs_nothing(parts, tmp_path):
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    runner = _Runner()
    lock = tmp_path / "held.lock"
    with gate(lock) as held:
        assert held is True
        out = _run(parts, runner, tmp_path, lock=lock)
    assert out["ran"] is False
    assert "lock" in out["reason"]
    assert runner.calls == []


# -- selecting and launching -------------------------------------------------


def test_a_pending_request_launches_triage(parts, tmp_path):
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    runner = _Runner()
    out = _run(parts, runner, tmp_path)
    assert out["ran"] is True
    assert out["session_type"] == TRIAGE
    dispatch, _brief = runner.calls[0]
    assert dispatch.session_type == TRIAGE
    assert dispatch.project == "m"


def test_the_brief_carries_the_scope_and_where_to_reach_the_unit(parts, tmp_path):
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    runner = _Runner()
    _run(parts, runner, tmp_path)
    _dispatch, brief = runner.calls[0]
    assert "http://127.0.0.1:9006" in brief
    assert "m" in brief
    assert TRIAGE in brief


def test_a_failed_session_is_reported_not_raised(parts, tmp_path):
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    out = _run(parts, _Runner(ok=False), tmp_path)
    assert out["ran"] is True
    assert out["ok"] is False


# -- collecting --------------------------------------------------------------


def test_the_run_is_recorded_in_the_lifecycle_log(parts, tmp_path):
    store, inbox, _, events = parts
    _seed_request(store, inbox)
    _run(parts, _Runner(), tmp_path)
    recorded = [e for e in events.list(project="m") if e.kind == SESSION]
    assert len(recorded) == 1
    assert recorded[0].facts["session_type"] == TRIAGE
    assert recorded[0].facts["ok"] is True
    assert recorded[0].facts["duration_seconds"] == 1.5


def test_the_run_ships_as_a_session_run_entry(parts, tmp_path):
    """session_run is what mu-logs rolls up avg_duration_seconds from --
    unlike project_event it is already in that unit's vocabulary."""
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    shipped = []
    _run(parts, _Runner(), tmp_path, shipper=lambda **kw: shipped.append(kw) or True)
    assert len(shipped) == 1
    assert shipped[0]["entry_type"] == "session_run"
    assert shipped[0]["payload"]["session_type"] == TRIAGE


def test_a_shipper_that_fails_does_not_fail_the_run(parts, tmp_path):
    """Logging is never the point of the call that triggered it."""
    store, inbox, _, _ = parts
    _seed_request(store, inbox)

    def boom(**_kw):
        raise RuntimeError("logs unit is down")

    out = _run(parts, _Runner(), tmp_path, shipper=boom)
    assert out["ran"] is True and out["ok"] is True


def test_the_gates_are_rechecked_after_the_session(parts, tmp_path):
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    out = _run(parts, _Runner(), tmp_path)
    assert "sound" in out["gates"]


# -- the brief ---------------------------------------------------------------


def test_a_brief_names_the_scope_fields_verbatim():
    d = Dispatch(
        DERIVATION, "m", {"layer": "A", "slice": "listings", "parents": ["B·01"]}, "why"
    )
    brief = d.brief("http://x")
    assert "layer" in brief and "A" in brief
    assert "listings" in brief
    assert "B·01" in brief
    assert "why" in brief


# -- the HTTP surface --------------------------------------------------------


def test_trigger_reports_when_nothing_is_eligible(store, tmp_path):
    """The gateway pokes this on a schedule. An empty poke is a normal
    answer, not an error."""
    from mu_spec.server import handle

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    status, _ct, raw = handle("POST", "/trigger", store, prompts, {})
    import json as _json

    assert status == 200
    assert _json.loads(raw)["ran"] is False


def test_trigger_is_a_post_only_route(store, tmp_path):
    from mu_spec.server import handle

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    status, _ct, _raw = handle("GET", "/trigger", store, prompts)
    assert status == 405


# -- readiness for a live run ------------------------------------------------


def test_a_failed_back_check_does_not_clear_the_correction(parts, tmp_path):
    """The session exists to stop an unvalidated correction flowing down. If
    a crashed run counted as validation it would do the opposite."""
    from mu_spec.dispatch import BACK_CHECK
    from mu_spec.lifecycle import SESSION

    store, inbox, _issues, events = parts
    store.create_project("m")
    events.record(
        SESSION, "m", lambda: 1.0, refs=["msg-0001"], session_type=BACK_CHECK, ok=False
    )
    assert service._unchecked_back_checked(events, "m") == set()

    events.record(
        SESSION, "m", lambda: 2.0, refs=["msg-0001"], session_type=BACK_CHECK, ok=True
    )
    assert service._unchecked_back_checked(events, "m") == {"msg-0001"}


def test_build_is_not_dispatched_without_a_target_repository(parts, tmp_path):
    """A build session runs with cwd inside mu-spec. Dispatching one with
    nowhere to write means writing the target project's code into this unit."""
    from mu_spec.dispatch import select
    from mu_spec.graph import Entry, Graph
    from mu_spec.identifiers import parse
    from mu_spec.storage import Manifest, Slice

    graph = Graph(
        [
            Entry(id=parse("I·01"), title="i"),
            Entry(id=parse("B·01"), derives_from=(parse("I·01"),), title="b"),
            Entry(id=parse("A·01"), derives_from=(parse("B·01"),), title="a"),
            Entry(id=parse("S·01"), derives_from=(parse("A·01"),), title="s"),
        ]
    )
    manifest = Manifest(
        project="m",
        slices={"listings": Slice(name="listings", members={parse(i) for i in ("B·01", "A·01", "S·01")})},
    )
    assert select(manifest, graph, can_build=False) is None
    assert select(manifest, graph, can_build=True).session_type == "build"


def test_a_dry_run_reports_the_dispatch_and_launches_nothing(parts, tmp_path):
    """So a brief can be read before it costs anything."""
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    runner = _Runner()
    out = _run(parts, runner, tmp_path, dry_run=True)
    assert out["ran"] is False
    assert out["would_run"] == TRIAGE
    assert "Session brief" in out["brief"]
    assert runner.calls == []


def test_a_dry_run_with_nothing_eligible_says_so(parts, tmp_path):
    out = _run(parts, _Runner(), tmp_path, dry_run=True)
    assert out["ran"] is False and out.get("would_run") is None


def test_the_brief_instructs_rather_than_only_describes():
    """The first live session read a purely descriptive brief as context and
    replied "I'm ready. What do you need?" -- then exited 0 having done
    nothing, which the loop recorded as success."""
    d = Dispatch(TRIAGE, "m", {"request": "msg-0001"}, "why")
    brief = d.brief("http://x")
    assert "Do this work now" in brief
    assert "headless" in brief.lower()


def test_the_brief_does_not_ask_the_session_to_fetch_its_own_contract():
    """SHARED.md is @-imported by each CLAUDE.md, because the repo's
    Read(../**) deny rule blocks a session from reading it as a file."""
    d = Dispatch(TRIAGE, "m", {"request": "msg-0001"}, "why")
    assert "read `../SHARED.md`" not in d.brief("http://x")


# -- progress detection ------------------------------------------------------


def test_a_session_that_changes_nothing_is_recorded_as_no_progress(parts, tmp_path):
    """Exit code is a bad proxy for progress. Four live sessions returned
    ok:true having achieved nothing -- one of them literally replied "I'm
    ready. What do you need?" and quit."""
    store, inbox, _, events = parts
    _seed_request(store, inbox)
    out = _run(parts, _Runner(), tmp_path)
    assert out["ok"] is True
    assert out["progress"] is False
    recorded = [e for e in events.list(project="m") if e.kind == SESSION]
    assert recorded[-1].facts["progress"] is False


def test_a_session_that_writes_entries_shows_progress(parts, tmp_path):
    store, inbox, issues, events = parts
    _seed_request(store, inbox)

    class _Working:
        """Stands in for a session that actually did its job."""

        def __call__(self, dispatch, brief):
            service.submit_amendment(
                store, inbox, "m",
                {"in_response_to": "msg-0001",
                 "entries": [{"layer": "I", "title": "a real intent entry"}]},
            )
            return SessionResult(dispatch.session_type, dispatch.project, True, 1.0, "")

    out = _run(parts, _Working(), tmp_path)
    assert out["progress"] is True


def test_a_dispatch_that_stalls_twice_is_not_launched_a_third_time(parts, tmp_path):
    """Without this the same no-op session is re-dispatched forever, each
    round costing real time and money to rediscover the same wall."""
    store, inbox, _, _ = parts
    _seed_request(store, inbox)
    runner = _Runner()
    for _ in range(service.STALL_CAP):
        assert _run(parts, runner, tmp_path)["ran"] is True
    out = _run(parts, runner, tmp_path)
    assert out["ran"] is False
    assert "stalled" in out["reason"]
    assert len(runner.calls) == service.STALL_CAP


def test_progress_clears_the_stall_count(parts, tmp_path):
    store, inbox, _, _ = parts
    _seed_request(store, inbox)

    class _Working:
        def __init__(self):
            self.n = 0

        def __call__(self, dispatch, brief):
            self.n += 1
            service.submit_amendment(
                store, inbox, "m",
                {"in_response_to": "msg-0001",
                 "entries": [{"layer": "I", "title": f"entry {self.n}"}]},
            )
            return SessionResult(dispatch.session_type, dispatch.project, True, 1.0, "")

    _run(parts, _Runner(), tmp_path)          # stall
    _run(parts, _Working(), tmp_path)         # progress -- resets
    assert _run(parts, _Runner(), tmp_path)["ran"] is True


def test_the_run_records_what_it_cost(parts, tmp_path):
    """24 live sessions burned 2.9 hours and an account session limit, and
    the only number kept was wall clock."""
    store, inbox, _, events = parts
    _seed_request(store, inbox)

    class _Costly:
        def __call__(self, dispatch, brief):
            return SessionResult(
                dispatch.session_type, dispatch.project, True, 1.0, "",
                {"cost_usd": 0.42, "output_tokens": 20,
                 "cache_creation_tokens": 27568},
            )

    out = _run(parts, _Costly(), tmp_path)
    assert out["cost_usd"] == 0.42
    recorded = [e for e in events.list(project="m") if e.kind == SESSION][-1]
    assert recorded.facts["cost_usd"] == 0.42
    assert recorded.facts["cache_creation_tokens"] == 27568


def test_the_loop_stops_when_the_budget_is_spent(parts, tmp_path):
    """Session 24 of the first full run died on an account limit rather than
    on any decision of ours. A cap is what makes that our choice."""
    store, inbox, _, events = parts
    _seed_request(store, inbox)

    class _Costly:
        def __call__(self, dispatch, brief):
            return SessionResult(dispatch.session_type, dispatch.project,
                                 True, 1.0, "", {"cost_usd": 3.0})

    assert _run(parts, _Costly(), tmp_path, budget_usd=5.0)["ran"] is True
    assert _run(parts, _Costly(), tmp_path, budget_usd=5.0)["ran"] is True
    out = _run(parts, _Costly(), tmp_path, budget_usd=5.0)
    assert out["ran"] is False
    assert "budget" in out["reason"]
    assert out["spent_usd"] == 6.0
