"""V3: a project's numbers, comparable against DARK's, reported and never gating.

The baseline is computed from an exported snapshot through the replay
loader, so DARK and the next project are measured by the same code over the
same kind of input. The real DARK run is the CLI's job, never this suite's.
"""

from __future__ import annotations

import json

from replay import baseline, compare


def _entry(ident, derives=(), depends=(), superseded_by=None, pi=False):
    return {"id": ident, "title": ident, "derives_from": list(derives),
            "depends_on": list(depends), "emits_into": [], "body": "",
            "superseded_by": superseded_by, "preserves_interface": pi}


def _derivation(seq, at, refs, slice_=None):
    return {"seq": seq, "at": at, "kind": "derivation", "refs": refs,
            "facts": {"slice": slice_}}


def _snapshot(root):
    """I-01 -> B-01 -> A-01 -> S-01, S-02 (in `extra`, depends on S-01) -> T-01.
    A correction at t=60 supersedes S-01 with S-03, touching both slices; a
    cut sits at t=45, before it."""
    (root / "entries").mkdir(parents=True)
    records = [
        _entry("I-01"), _entry("B-01", ["I-01"]), _entry("A-01", ["B-01"]),
        _entry("S-01", ["A-01"], superseded_by="S-03"),
        _entry("S-02", ["A-01"], ["S-01"]),
        _entry("T-01", ["S-02"]),
        _entry("S-03", ["A-01"], pi=True),
    ]
    for raw in records:
        (root / "entries" / f"{raw['id']}.json").write_text(json.dumps(raw), encoding="utf-8")
    events = [
        _derivation(1, 10.0, ["I-01"]), _derivation(2, 20.0, ["B-01"]),
        _derivation(3, 30.0, ["A-01"], "core"), _derivation(4, 40.0, ["S-01"], "core"),
        _derivation(5, 41.0, ["S-02"], "extra"), _derivation(6, 42.0, ["T-01"]),
        _derivation(7, 60.0, ["S-03"], "core"),
    ]
    (root / "events.json").write_text(json.dumps({"events": events}), encoding="utf-8")
    (root / "cuts.json").write_text(json.dumps({"cuts": [{"seq": 1, "at": 45.0}]}), encoding="utf-8")
    (root / "gates.json").write_text(json.dumps({"project": "toy"}), encoding="utf-8")
    messages = [
        {"id": "msg-0001", "type": "initiate", "title": "t", "project": "toy", "at": 5.0,
         "resolution": {"produced": ["I-01", "B-01", "A-01", "S-01", "S-02", "T-01"]}},
        {"id": "msg-0002", "type": "correction", "title": "t", "project": "toy", "at": 55.0,
         "resolution": {"produced": ["S-03"]}},
    ]
    (root / "inbox.json").write_text(json.dumps({"messages": messages}), encoding="utf-8")
    issues = [
        {"id": "iss-0001", "project": "toy", "kind": "semantic", "at": 44.0},
        {"id": "iss-0002", "project": "toy", "kind": "additive", "at": 50.0},
    ]
    (root / "issues.json").write_text(json.dumps({"project": "toy", "issues": issues}), encoding="utf-8")
    return root


def test_the_final_state_is_measured_from_the_replayed_graph(tmp_path):
    result = baseline.compute(_snapshot(tmp_path / "snap"))
    m = result["metrics"]
    assert m["live_entries"] == 6
    assert m["live_spec"] == 2 and m["live_test"] == 1
    assert m["supersessions"] == 1
    assert m["supersessions_per_live_entry"] == round(1 / 6, 3)
    assert m["slices"] == 2
    assert m["wave_depth"] == 2  # extra depends on core
    # msg-0001 touched core and extra; msg-0002 only core.
    assert m["changes"] == 2
    assert m["change_locality_mean"] == 1.5
    assert m["single_slice_share"] == 0.5
    assert m["corrections"] == 1 and m["corrections_at_spec"] == 1
    assert m["corrections_at_intent"] == 0
    # The one live dependency edge, S-02 -> S-03, leaves its slice.
    assert m["cohesion_weighted"] == 0.0
    assert m["issues"] == 2 and m["issues_semantic"] == 1
    assert m["issues_per_live_spec"] == 1.0
    assert result["provenance"]["project"] == "toy"


def test_the_replay_is_checked_against_the_nodes_own_insights(tmp_path):
    snap = _snapshot(tmp_path / "snap")
    (snap / "insights.json").write_text(json.dumps({
        "change_locality": {"changes": 2, "single_slice": 1, "mean": 1.5},
        "corrections": {"corrections": 1, "by_layer": {"spec": 1}},
        "slices": [{"internal_edges": 0, "outbound_edges": 1}, {}],
        "issues": {"total": 3, "semantic": 1},  # the replay counts 2
        "waves": [{}, {}],
    }), encoding="utf-8")
    check = baseline.compute(snap)["provenance"]["disagrees_with_node_insights"]
    assert check == ["issues: node 3 != replay 2"]


def test_the_cli_writes_one_line_per_cut(tmp_path):
    out = tmp_path / "b.json"
    assert baseline.main([str(_snapshot(tmp_path / "snap")), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["per_cut"][0]["cut"] == 1 and data["metrics"]["live_entries"] == 6


def test_a_cut_is_measured_on_what_existed_when_it_was_taken(tmp_path):
    cut = baseline.compute(_snapshot(tmp_path / "snap"))["per_cut"][0]
    assert cut["cut"] == 1
    m = cut["metrics"]
    assert m["supersessions"] == 0
    assert m["live_entries"] == 6
    assert m["corrections"] == 0
    assert m["changes"] == 1
    assert m["issues"] == 1


def test_a_live_project_reads_the_same_numbers_from_insights_and_spine():
    insights = {
        "change_locality": {"changes": 4, "single_slice": 3, "mean": 1.25},
        "corrections": {"corrections": 2, "by_layer": {"behaviour": 2}},
        "slices": [{"internal_edges": 3, "outbound_edges": 1},
                   {"internal_edges": 0, "outbound_edges": 0}],
        "issues": {"total": 3, "semantic": 1},
        "waves": [{"wave": 0}, {"wave": 1}, {"wave": 2}],
    }
    spine = [{"id": "I-01"}, {"id": "S-01"}, {"id": "S-02"}, {"id": "T-01"}]
    m = baseline.summarise(insights, spine)
    assert m["supersessions"] is None  # not over HTTP: history is not served
    assert m["cohesion_weighted"] == 0.75
    assert m["wave_depth"] == 3
    assert m["corrections_at_behaviour"] == 2
    assert m["issues_per_live_spec"] == 1.5


def test_forge_ledger_numbers_are_normalised_per_unit():
    retro = {
        "units_touched": 4,
        "events": {"stopped": 2},
        "rulings": {"spec_fault": 1},
        "rulings_spec_fault_without_issue": 1,
        "permission_denials_by_tool": {"Bash": 6, "Edit": 2},
        "max_attempt_per_unit": {"1": 3, "2": 1},
        "driver_idle_gaps_over_1h": [{"hours": 2.0}, {"hours": 1.5}],
        "cost_usd_total": 10.0,
        "lead_time_h_admitted_to_done": {"p50": 0.2},
    }
    ledger = baseline.ledger(retro)
    assert ledger["stops_per_unit"] == 0.5
    assert ledger["spec_fault_rulings_per_unit"] == 0.25
    assert ledger["spec_fault_rulings_without_issue"] == 1
    assert ledger["denials_per_unit"] == 2.0
    assert ledger["first_attempt_share"] == 0.75
    assert ledger["idle_gaps_over_1h"] == 2 and ledger["idle_hours_over_1h"] == 3.5
    assert ledger["cost_usd_per_unit"] == 2.5
    assert ledger["lead_time_p50_h"] == 0.2
    # A flat ledger, already in these names, passes through untouched.
    assert baseline.ledger({"stops_per_unit": 0.1}) == {"stops_per_unit": 0.1}


def test_the_table_shows_both_sides_the_delta_and_which_way_is_better():
    base = {"metrics": {"change_locality_mean": 2.4, "supersessions": 170},
            "ledger": {"stops_per_unit": 0.25}}
    current = {"metrics": {"change_locality_mean": 1.2, "supersessions": 200},
               "ledger": {}}
    rows = {r[0]: r for r in compare.rows(base, current)}
    name, b, c, delta, better, verdict = rows["change_locality_mean"]
    assert (b, c, delta, better, verdict) == ("2.4", "1.2", "-1.2", "lower", "better")
    assert rows["supersessions"][3:] == ("+30", "lower", "worse")
    # Missing on one side degrades to n/a, never an error.
    assert rows["stops_per_unit"][1:] == ("0.25", "n/a", "n/a", "lower", "")
    assert rows["live_entries"][4:] == ("", "")  # context only, no direction
    text = compare.render(base, current)
    assert "change_locality_mean" in text and "forge ledger" in text


def test_the_cli_compares_two_files_and_takes_a_ledger_for_the_current_side(tmp_path, capsys):
    base = tmp_path / "base.json"
    current = tmp_path / "cur.json"
    ledger = tmp_path / "ledger.json"
    base.write_text(json.dumps({"metrics": {"corrections": 4}, "ledger": {"stops_per_unit": 0.25}}))
    current.write_text(json.dumps({"metrics": {"corrections": 1}}))
    ledger.write_text(json.dumps({"units_touched": 10, "events": {"stopped": 1}}))
    assert compare.main([str(base), str(current), "--ledger", str(ledger)]) == 0
    out = capsys.readouterr().out
    assert "0.25" in out and "0.1" in out
    # Report-only: a worse number never changes the exit code.
    current.write_text(json.dumps({"metrics": {"corrections": 99}}))
    assert compare.main([str(base), str(current)]) == 0


def test_a_snapshot_directory_can_stand_as_the_current_side(tmp_path, capsys):
    snap = _snapshot(tmp_path / "snap")
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"metrics": {"live_entries": 5}}))
    assert compare.main([str(base), str(snap)]) == 0
    assert "+1" in capsys.readouterr().out
