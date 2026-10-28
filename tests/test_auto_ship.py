"""Delegated Ship (M33, D2): an agent may cut and Ship on its own judgement only
while a person's delegation stands and the change since the last shipped cut
is S/T only, on units already out, with every gate green. Anything else is the
person's press, refused here with the condition that failed and no write.
"""

from __future__ import annotations

import json

import pytest

from mu_spec.emit import Runs
from mu_spec.graph import Entry
from mu_spec.identifiers import parse
from mu_spec.lifecycle import Lifecycle
from mu_spec.server import _actions, _tools, handle
from mu_spec.storage import ProjectStore
from test_sync import Tracker

P = "p"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    store = ProjectStore(tmp_path / "projects")
    store.create_project(P)
    store.append(P, [Entry(id=parse("I-01"), title="intent")])
    store.append(P, [Entry(id=parse("B-01"), derives_from=(parse("I-01"),),
                           title="behaviour")], slice_name="core")
    store.append(P, [Entry(id=parse("A-01"), derives_from=(parse("B-01"),),
                           title="arch")], slice_name="core")
    store.append(P, [Entry(id=parse("S-01"), derives_from=(parse("A-01"),),
                           title="the contract", body="build it")], slice_name="core")
    store.append(P, [Entry(id=parse("T-01"), derives_from=(parse("S-01"),),
                           title="the case", purpose="why")])
    store.set_module(P, "app/thing.py", ["S-01"])
    store.set_module(P, "tests/test_thing.py", ["T-01"])
    store.set_repo(P, "o/dark")
    store.set_labels(P, [], pickup=["cto:admitted"])
    gh = Tracker()
    events = Lifecycle(store.events_path())

    def call(method, path, body=None, runs=None):
        status, ctype, raw = handle(method, path, store, tmp_path, body,
                                    now_fn=lambda: 5.0, events=events, runs=runs,
                                    emit_client=gh)
        return status, (json.loads(raw) if ctype == "application/json" else raw)

    # A person cuts and ships, then delegates.
    assert call("POST", f"/projects/{P}/units/cut", {"actor": "andrey"})[0] == 200
    assert call("POST", f"/projects/{P}/emit", {"actor": "andrey"})[0] == 200
    return store, gh, events, call


def _grant(call, enabled=True, actor="andrey"):
    return call("POST", f"/projects/{P}/delegation",
                {"enabled": enabled, "note": "S/T repairs", "actor": actor})


def _s_repair(store):
    """An S/T correction of shipped units: S-02 supersedes S-01, and T-02
    supersedes T-01 so the scenario judges the live contract."""
    store.append(P, [Entry(id=parse("S-02"), derives_from=(parse("A-01"),),
                           title="the contract", body="build it, corrected",
                           supersedes=parse("S-01"))], slice_name="core")
    store.append(P, [Entry(id=parse("T-02"), derives_from=(parse("S-02"),),
                           title="the case", purpose="why",
                           supersedes=parse("T-01"))])


def _footprint(store, gh, events):
    return (store.units_path(P).read_text(encoding="utf-8"),
            store.emissions_path(P).read_text(encoding="utf-8"),
            len(gh.writes()),
            [e.kind for e in events.list(P) if e.kind != "mutation"])


def _refused(env, failed):
    store, gh, events, call = env
    before = _footprint(store, gh, events)
    status, payload = call("POST", f"/projects/{P}/auto_ship", {"actor": "lain"})
    assert status == 409, payload
    assert payload["failed"] == failed, payload
    assert _footprint(store, gh, events) == before, "a refusal writes nothing"
    return payload


def test_delegation_off_refuses(env):
    store, gh, events, call = env
    _s_repair(store)
    payload = _refused(env, ["delegation"])
    assert "delegation" in payload["reason"]


def test_a_revoked_delegation_refuses(env):
    store, gh, events, call = env
    _grant(call)
    _grant(call, enabled=False)
    _s_repair(store)
    _refused(env, ["delegation"])


def test_all_conditions_hold_cut_and_press_happen_and_are_recorded(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    status, payload = call("POST", f"/projects/{P}/auto_ship", {"actor": "lain"})
    assert status == 200, payload
    assert payload["emitted"] is True
    assert payload["cut_seq"] == 2
    assert "S-01" in [e["key"] for e in payload["edited"]]
    assert any("build it, corrected" in i["body"] for i in gh.issues.values())
    assert payload["created"] == [], "a delegated press never opens new work"

    kinds = [e.kind for e in events.list(P)]
    assert kinds.count("work_units") == 2
    auto = [e for e in events.list(P, kind="auto_ship")]
    assert len(auto) == 1
    facts = auto[0].facts
    assert facts["actor"] == "lain"
    assert facts["cut_seq"] == 2 and facts["base_cut"] == 1
    assert set(facts["conditions"]) == {"delegation", "ready", "gates", "st_only", "shipped",
                                        "tests_assigned"}
    assert all(c["ok"] for c in facts["conditions"].values())
    assert facts["conditions"]["shipped"]["touched"] == ["S-01", "S-01-T"]
    # The Ship completion event forge waits on, the same as a person's press.
    shipped = events.list(P, kind="tickets_emitted")
    assert len(shipped) == 2 and shipped[-1].facts["cut_seq"] == 2


def test_as_a_job_it_answers_with_the_run_and_completes(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    runs = Runs(spawn=lambda fn: fn())
    status, payload = call("POST", f"/projects/{P}/auto_ship", {"actor": "lain"}, runs=runs)
    assert status == 202, payload
    assert payload["started"] is True and payload["run_id"] and payload["cut_seq"] == 2
    assert runs.status(P)["result"]["emitted"] is True
    assert events.list(P, kind="tickets_emitted")[-1].facts["cut_seq"] == 2


def test_an_unsound_graph_refuses_on_the_gates(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    store.append(P, [Entry(id=parse("S-03"), title="an orphan")], slice_name="core")
    payload = _refused(env, ["gates"])
    assert payload["conditions"]["gates"]["sound"] is False


def test_a_dead_end_citation_refuses_on_the_gates(env, monkeypatch):
    from mu_spec import service

    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    dead = [{"key": "S-01", "retired": "S-09", "successor": None, "in": "S-02"}]
    monkeypatch.setattr(service, "_retired_citations", lambda *a, **k: dead)
    payload = _refused(env, ["gates"])
    assert payload["conditions"]["gates"]["dead_ends"] == dead


def test_a_dead_blocker_refuses_on_the_gates(env):
    """S-01 waits on S-01-T; once the test unit's issue is closed not_planned
    in the record, the press would leave S-01 waiting forever."""
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    lines = store.emissions_path(P).read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record["issues"]["S-01-T"]["settled"] = {"state_reason": "not_planned"}
    store.emissions_path(P).write_text(json.dumps(record) + "\n", encoding="utf-8")
    payload = _refused(env, ["gates"])
    assert payload["conditions"]["gates"]["dead_blockers"] == [
        {"key": "S-01", "blocker": "S-01-T"}]


@pytest.mark.parametrize("entry", [
    Entry(id=parse("I-02"), title="another intent"),
    Entry(id=parse("B-02"), derives_from=(parse("I-01"),), title="another behaviour"),
    Entry(id=parse("A-02"), derives_from=(parse("B-01"),), title="another arch"),
])
def test_an_i_b_or_a_change_refuses(env, entry):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    store.append(P, [entry], slice_name=None if entry.id.layer == "I" else "core")
    payload = _refused(env, ["st_only"])
    assert payload["conditions"]["st_only"]["changed"] == {
        entry.id.layer: [str(entry.id)]}


def test_a_change_touching_an_unshipped_unit_refuses(env):
    store, gh, events, call = env
    _grant(call)
    store.append(P, [Entry(id=parse("S-03"), derives_from=(parse("A-01"),),
                           title="new work", body="more")], slice_name="core")
    store.set_module(P, "app/new.py", ["S-03"])
    payload = _refused(env, ["shipped"])
    assert payload["conditions"]["shipped"]["unshipped"] == ["S-03"]


def test_a_shipped_cut_from_before_marks_cannot_be_compared(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    path = store.units_path(P)
    raw = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    for r in raw:
        r.pop("marks", None)
    path.write_text("".join(json.dumps(r) + "\n" for r in raw), encoding="utf-8")
    payload = _refused(env, ["st_only"])
    assert "predates" in payload["conditions"]["st_only"]["reason"]


def test_the_agent_manifest_offers_auto_ship_and_the_read_never_the_grant():
    tools = {(t["method"], t["path"]): t["name"] for t in _tools()}
    assert tools[("POST", "/projects/{project}/auto_ship")] == "auto_ship"
    assert tools[("GET", "/projects/{project}/delegation")] == "get_delegation"
    assert ("POST", "/projects/{project}/delegation") not in tools
    assert "set_delegation" not in tools.values()
    assert {a["name"]: a["path"] for a in _actions()}["set_delegation"] == \
        "projects/{project}/delegation"


def test_the_change_log_records_grant_and_revoke_with_the_actor(env):
    store, gh, events, call = env
    assert _grant(call)[1]["delegation"]["enabled"] is True
    status, payload = _grant(call, enabled=False, actor="andrey-2")
    assert status == 200 and payload["delegation"]["enabled"] is False
    log = [e.facts for e in events.list(P, kind="mutation")
           if e.facts["route"] == "set_delegation"]
    assert [(f["actor"], f["body"]["enabled"]) for f in log] == [
        ("andrey", True), ("andrey-2", False)]
    assert log[1]["was"]["delegation"]["enabled"] is True
    granted = events.list(P, kind="delegation")
    assert [(e.facts["enabled"], e.facts["actor"]) for e in granted] == [
        (True, "andrey"), (False, "andrey-2")]
    _, read = call("GET", f"/projects/{P}/delegation")
    assert read["delegation"]["enabled"] is False and read["delegation"]["actor"] == "andrey-2"


def test_a_grant_must_say_enabled_true_or_false(env):
    store, gh, events, call = env
    status, payload = call("POST", f"/projects/{P}/delegation", {"enabled": "yes"})
    assert status == 400 and "enabled" in payload["error"]


# -- tests_assigned: a T with no test unit to carry it (Andrey, 10-28) -------
#
# A GUARD on the delegated press, never a gate on a person's: a person may
# ship ahead of the tests, and is told.


def _unassigned_t(store, judges="S-02"):
    """T-03 judges the live contract and no test module declares it."""
    store.append(P, [Entry(id=parse("T-03"), derives_from=(parse(judges),),
                           title="a stray case", purpose="why")])


def _fixture_t(store):
    """T-03 is declared, but names fixture files no module declares."""
    store.append(P, [Entry(id=parse("T-03"), derives_from=(parse("S-02"),),
                           title="a fixture case", purpose="why",
                           body="Load every `tests/fixtures/study/*.toml` and "
                           "`tests/fixtures/one.json`.")])
    held = store.load_manifest(P).modules["tests/test_thing.py"]
    store.set_module(P, "tests/test_thing.py", sorted(map(str, held)) + ["T-03"])


def test_a_t_in_no_test_module_refuses_on_tests_assigned(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    _unassigned_t(store)
    payload = _refused(env, ["tests_assigned"])
    c = payload["conditions"]["tests_assigned"]
    assert c["unassigned_tests"] == ["T-03"] and c["undeclared_test_paths"] == []


def test_a_t_naming_an_undeclared_file_refuses_on_tests_assigned(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    _fixture_t(store)
    payload = _refused(env, ["tests_assigned"])
    assert payload["conditions"]["tests_assigned"]["undeclared_test_paths"] == [
        {"t": "T-03", "path": "tests/fixtures/study/"},
        {"t": "T-03", "path": "tests/fixtures/one.json"}]


def test_tests_assigned_holds_once_the_fixtures_are_declared(env):
    store, gh, events, call = env
    _grant(call)
    _s_repair(store)
    _fixture_t(store)
    store.set_module(P, "tests/fixtures/study/a.toml", ["T-03"])
    store.set_module(P, "tests/fixtures/one.json", ["T-03"])
    status, payload = call("POST", f"/projects/{P}/auto_ship", {"actor": "lain"})
    assert status == 200, payload


def test_a_persons_press_ships_ahead_of_its_tests_and_is_told(env):
    store, gh, events, call = env
    _unassigned_t(store, judges="S-01")
    store.append(P, [Entry(id=parse("T-04"), derives_from=(parse("S-01"),),
                           title="fixture", purpose="why",
                           body="reads `tests/fixtures/x.toml`")])
    store.set_module(P, "tests/test_other.py", ["T-04"])
    assert call("POST", f"/projects/{P}/units/cut", {"actor": "andrey"})[0] == 200
    status, payload = call("POST", f"/projects/{P}/emit", {"actor": "andrey"})
    assert status == 200 and payload["emitted"] is True, payload
    assert payload["unassigned_tests"] == ["T-03"]
    assert payload["undeclared_test_paths"] == [
        {"t": "T-04", "path": "tests/fixtures/x.toml"}]
    _, preview = call("GET", f"/projects/{P}/emit")
    assert preview["unassigned_tests"] == ["T-03"]
    assert preview["undeclared_test_paths"] == payload["undeclared_test_paths"]


def test_a_clean_preview_carries_empty_lists(env):
    store, gh, events, call = env
    _, preview = call("GET", f"/projects/{P}/emit")
    assert preview["unassigned_tests"] == [] and preview["undeclared_test_paths"] == []
