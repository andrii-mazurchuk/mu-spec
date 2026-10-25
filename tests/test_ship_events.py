"""Every finished Ship run leaves exactly one durable event (Andrey, 10-25):
`tickets_emitted` on success, `ship_failed` on an exception or a refusal, both
carrying the run_id the 202 answered with. The in-memory run status dies with
the process; these do not, so a caller polling after a restart can tell
"failed" from "never ran"."""

from __future__ import annotations

from mu_spec.emit import Runs

from test_auto_ship import P, _grant, _s_repair, env  # noqa: F401  (fixture)


def _press(call, path="emit", body=None):
    runs = Runs(spawn=lambda fn: fn())
    status, payload = call("POST", f"/projects/{P}/{path}", body or {}, runs=runs)
    assert status == 202, payload
    return payload["run_id"], runs


def _ship_events(events, run_id):
    return [e for e in events.list(P)
            if e.kind in ("tickets_emitted", "ship_failed") and e.facts.get("run_id") == run_id]


def test_a_successful_run_records_tickets_emitted_with_its_run_id(env):
    store, gh, events, call = env
    run_id, runs = _press(call)
    assert runs.status(P)["result"]["emitted"] is True
    [event] = _ship_events(events, run_id)
    assert event.kind == "tickets_emitted"
    assert event.facts["cut_seq"] == 1 and event.facts["repo"] == "o/dark"


def test_a_refused_run_records_ship_failed_with_the_reason(env):
    store, gh, events, call = env
    # The graph moves after the cut: the press refuses on drift.
    _s_repair(store)
    run_id, runs = _press(call)
    assert runs.status(P)["result"]["emitted"] is False
    [event] = _ship_events(events, run_id)
    assert event.kind == "ship_failed"
    assert "moved since cut 1" in event.facts["reason"]
    assert event.facts["cut_seq"] == 1 and "error" not in event.facts


def test_a_run_that_raises_records_ship_failed_with_the_error(env):
    store, gh, events, call = env

    def down(repo):
        raise RuntimeError("tracker down")

    gh.can_label = down
    run_id, runs = _press(call)
    assert runs.status(P)["phase"] == "failed"
    [event] = _ship_events(events, run_id)
    assert event.kind == "ship_failed"
    assert "tracker down" in event.facts["error"]
    assert event.facts["cut_seq"] == 1 and "processed" in event.facts


def test_one_unit_mode_is_covered_too(env):
    store, gh, events, call = env
    run_id, _ = _press(call, body={"unit": "no-such-unit-at-all"})
    assert len(_ship_events(events, run_id)) == 1


def test_auto_ship_records_its_run(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    run_id, _ = _press(call, "auto_ship", {"actor": "lain"})
    [event] = _ship_events(events, run_id)
    assert event.kind == "tickets_emitted" and event.facts["cut_seq"] == 2


def test_rollback_writes_no_ship_event(env):
    store, gh, events, call = env
    runs = Runs(spawn=lambda fn: fn())
    status, payload = call("POST", f"/projects/{P}/emit/rollback", {"cut_seq": 1}, runs=runs)
    assert status == 202, payload
    assert _ship_events(events, payload["run_id"]) == []
