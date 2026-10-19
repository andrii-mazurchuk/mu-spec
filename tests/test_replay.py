"""The replay harness, on a snapshot small enough to reason about by hand.

The harness rebuilds every historical state of a project from an exported
snapshot and runs gates over each one. Everything it scores rests on the
reconstruction being right, so the reconstruction is what these tests pin:
an identifier saved twice under two spellings is one entry, an edge the
export redirected to a successor is walked back to what the child cited at
birth, membership is read as of a time, and a slice's type before a
classification is the other one. The real DARK run is the CLI's job, never
this suite's.
"""

from __future__ import annotations

import json

import pytest

from mu_spec.identifiers import parse
from mu_spec.storage import CROSS_CUTTING, SLICE
from replay import harness


def _entry(ident, derives=(), depends=(), body="", superseded_by=None, pi=False):
    raw = {
        "id": ident,
        "layer": "x",
        "title": ident,
        "derives_from": list(derives),
        "depends_on": list(depends),
        "emits_into": [],
        "body": body,
        "superseded_by": superseded_by,
        "children": [],
    }
    if pi:
        raw["preserves_interface"] = True
    return raw


def _derivation(seq, at, refs, slice_=None):
    return {"seq": seq, "at": at, "kind": "derivation", "refs": refs,
            "facts": {"slice": slice_, "layers": []}}


CLEAN = {"project": "toy", "sound": True, "complete": True, "clean": True,
         "findings": [], "slice_findings": []}


def _snapshot(root, gates=CLEAN):
    """I-01 -> B-01 -> A-01 -> S-01/S-02 -> T-01/T-02, then S-01 is corrected
    into S-03 (interface-preserving), S-02 moves to a slice that starts out
    cross-cutting and is later classified a plain slice.

    The export carries what a live read showed: S-02's dependency and T-01's
    parent already name S-03, though both were written against S-01."""
    entries = root / "entries"
    entries.mkdir(parents=True)
    records = {
        "I-01": _entry("I-01"),
        "B-01": _entry("B-01", ["I-01"]),
        "A-01": _entry("A-01", ["B-01"]),
        "S-01": _entry("S-01", ["A-01"], body="Module: `pkg/a.py`. Imports nothing.",
                       superseded_by="S-03"),
        # The legacy spelling, saved under both file names, as the real
        # export did.
        "S·02": _entry("S·02", ["A-01"], ["S-03"], body="Module: `pkg/b.py`."),
        "T-01": _entry("T-01", ["S-03"]),
        "T-02": _entry("T-02", ["S·02"]),
        "S-03": _entry("S-03", ["A-01"], body="Module: `pkg/a.py`.", pi=True),
    }
    for ident, raw in records.items():
        (entries / f"{ident}.json").write_text(json.dumps(raw), encoding="utf-8")
    (entries / "S.02.json").write_text(json.dumps(records["S·02"]), encoding="utf-8")

    events = [
        _derivation(1, 10.0, ["I·01"]),
        _derivation(2, 20.0, ["B-01"]),
        _derivation(3, 30.0, ["A-01"], "core"),
        _derivation(4, 40.0, ["S-01"], "core"),
        _derivation(5, 50.0, ["S·02"], "core"),
        _derivation(6, 60.0, ["T-01"]),
        {"seq": 7, "at": 70.0, "kind": "correction", "refs": ["S-01"],
         "facts": {"replaced_by": ["S-03"]}},
        _derivation(8, 70.5, ["S-03"], "core"),
        {"seq": 9, "at": 80.0, "kind": "slicing", "refs": ["S-02"],
         "facts": {"source": "core", "into": "extra"}},
        {"seq": 10, "at": 90.0, "kind": "classification", "refs": ["extra"],
         "facts": {"type": "slice"}},
        _derivation(11, 92.0, ["T-02"]),
        {"seq": 12, "at": 100.1, "kind": "work_units", "refs": [], "facts": {"seq": 1}},
    ]
    (root / "events.json").write_text(json.dumps({"events": events}), encoding="utf-8")
    (root / "cuts.json").write_text(
        json.dumps({"cuts": [{"seq": 1, "at": 100.0, "note": "", "units": 3}]}),
        encoding="utf-8")
    (root / "gates.json").write_text(json.dumps(gates), encoding="utf-8")
    (root / "waves.json").write_text(json.dumps({
        "waves": [{"wave": 0, "slices": ["core"]}, {"wave": 1, "slices": ["extra"]}],
        "unschedulable": []}), encoding="utf-8")
    (root / "units.json").write_text(json.dumps({"cut": {"seq": 1, "at": 100.0, "units": [
        {"entries": ["S-02"]}, {"entries": ["S-03"]}, {"entries": ["T-01", "T-02"]}]}}),
        encoding="utf-8")
    return root


@pytest.fixture
def history(tmp_path):
    return harness.load(_snapshot(tmp_path / "snap"))


def _ids(graph):
    return {str(e.id) for e in graph.entries()}


def test_an_identifier_saved_under_two_spellings_is_one_entry(history):
    assert sorted(history.entries) == [
        "A-01", "B-01", "I-01", "S-01", "S-02", "S-03", "T-01", "T-02"]
    assert history.born["S-02"] == 50.0


def test_two_files_for_one_id_that_disagree_fail_loudly(tmp_path):
    root = _snapshot(tmp_path / "snap")
    (root / "entries" / "S.02.json").write_text(
        json.dumps(_entry("S·02", ["A-01"], body="something else")), encoding="utf-8")
    with pytest.raises(harness.SnapshotError, match="S-02"):
        harness.load(root)


def test_a_redirected_edge_is_walked_back_to_what_the_child_cited_at_birth(history):
    # S-02 and T-01 were written before S-03 existed.
    assert history.entries["S-02"].depends_on == (parse("S-01"),)
    assert history.entries["T-01"].derives_from == (parse("S-01"),)
    # T-02 cites S-02 in the legacy spelling; it was never redirected.
    assert history.entries["T-02"].derives_from == (parse("S-02"),)


def test_membership_is_read_as_of_a_time(history):
    graph, _ = harness.build(history, 45.0)
    assert _ids(graph) == {"I-01", "B-01", "A-01", "S-01"}

    graph, _ = harness.build(history, 75.0)
    assert _ids(graph) == {"I-01", "B-01", "A-01", "S-02", "S-03", "T-01"}
    assert graph.superseded_by(parse("S-01")) == parse("S-03")
    # The live read carries the preserving supersession, as mu-spec does.
    assert graph.get(parse("S-02")).depends_on == (parse("S-03"),)


def test_slices_follow_derivation_then_slicing_events_and_b_follows_its_children(history):
    _, manifest = harness.build(history, 75.0)
    assert manifest.slice_of(parse("S-02")) == "core"
    assert manifest.slice_of(parse("B-01")) == "core"
    assert manifest.slice_of(parse("I-01")) is None
    assert manifest.slice_of(parse("T-01")) is None

    _, manifest = harness.build(history, 85.0)
    assert manifest.slice_of(parse("S-02")) == "extra"


def test_a_classified_slice_had_the_other_type_before(history):
    _, before = harness.build(history, 85.0)
    _, after = harness.build(history, 95.0)
    assert before.slices["extra"].type == CROSS_CUTTING
    assert after.slices["extra"].type == SLICE
    assert after.slices["core"].type == SLICE


def test_modules_are_read_from_live_spec_bodies(history):
    _, manifest = harness.build(history, 75.0)
    assert manifest.modules == {"pkg/a.py": {parse("S-03")}, "pkg/b.py": {parse("S-02")}}


def test_there_is_one_step_per_graph_changing_event(history):
    steps = harness.steps(history)
    # The correction (7) retires nothing by itself: S-01 retires when S-03,
    # the successor it names, is born (8).
    assert [s.seq for s in steps] == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11]
    assert steps[-1].cut == 0
    assert harness.step_index(steps, 12) == len(steps) - 1


def test_the_self_check_passes_on_a_consistent_snapshot(history):
    assert harness.self_check(history) == []


def test_the_self_check_fails_loudly_when_the_final_state_disagrees(tmp_path):
    root = _snapshot(tmp_path / "snap", gates={**CLEAN, "sound": False})
    problems = harness.self_check(harness.load(root))
    assert any("gates.json" in p for p in problems)


def test_the_sound_gate_sees_the_cross_cutting_window(history):
    flags = harness.run(history, {"sound": harness.sound})
    steps = harness.steps(history)
    flagged = {s.seq for s, ids in zip(steps, flags["sound"]) if "slice:extra" in ids}
    # extra is cross-cutting from the slicing event until it is classified,
    # and S-02 in it depends on core.
    assert flagged == {9}


def test_scoring_counts_a_case_only_when_every_expected_id_is_flagged_in_its_window():
    steps = [harness.Step(seq=s, at=float(s), kind="derivation", cut=0) for s in (1, 2, 3)]
    flags = {"g": [{"S-01"}, {"S-01", "S-02"}, {"S-03", "S-99"}]}
    cases = [
        {"finding_id": "both", "gate_ticket": "M2", "expected_ids": ["S-01", "S-02"],
         "earliest_step_visible": 1},
        {"finding_id": "late", "gate_ticket": "M3", "expected_ids": ["S-03"],
         "earliest_step_visible": 3},
        {"finding_id": "before-window", "gate_ticket": "M3", "expected_ids": ["S-01"],
         "earliest_step_visible": 3},
        {"finding_id": "never", "gate_ticket": "M5", "expected_ids": ["S-04"],
         "earliest_step_visible": 1},
    ]
    report = harness.score(steps, flags, cases)["g"]
    by_case = {c["finding_id"]: c for c in report["cases"]}
    assert by_case["both"]["first_step"] == 2
    assert by_case["late"]["first_step"] == 3
    assert by_case["before-window"]["first_step"] is None
    assert by_case["never"]["first_step"] is None
    assert report["caught"] == 2 and report["missed"] == 2
    assert report["by_ticket"] == {"M2": [1, 1], "M3": [1, 2], "M5": [0, 1]}
    # Only S-99 was flagged without any case expecting it.
    assert report["false_positives"] == ["S-99"]
    assert report["flagged_steps"] == 3


def test_a_case_may_expect_a_slice_finding(history):
    flags = {"g": [set()] * 9 + [{"slice:extra"}]}
    steps = harness.steps(history)
    case = {"finding_id": "s", "gate_ticket": "M8", "expected_ids": ["slice:extra"],
            "earliest_step_visible": 9}
    harness.check_oracle(history, [case])
    assert harness.score(steps, flags, [case])["g"]["cases"][0]["first_step"] == 11
    with pytest.raises(harness.SnapshotError, match="slice:nowhere"):
        harness.check_oracle(history, [{**case, "expected_ids": ["slice:nowhere"]}])


def test_an_oracle_naming_an_entry_the_snapshot_lacks_is_refused(history):
    bad = [{"finding_id": "x", "gate_ticket": "M2", "expected_ids": ["S-42"],
            "earliest_step_visible": 5}]
    with pytest.raises(harness.SnapshotError, match="S-42"):
        harness.check_oracle(history, bad)
    unborn = [{"finding_id": "y", "gate_ticket": "M2", "expected_ids": ["S-03"],
               "earliest_step_visible": 5}]
    with pytest.raises(harness.SnapshotError, match="S-03"):
        harness.check_oracle(history, unborn)


def test_the_cli_writes_a_report(tmp_path, capsys):
    root = _snapshot(tmp_path / "snap")
    oracle = tmp_path / "oracle.json"
    oracle.write_text(json.dumps({"cases": [
        {"finding_id": "f", "pattern_id": "p", "gate_ticket": "M5",
         "expected_ids": ["S-02"], "earliest_step_visible": 9, "note": ""}]}),
        encoding="utf-8")
    out = tmp_path / "report.json"

    assert harness.main([str(root), str(oracle), "--out", str(out)]) == 0

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["self_check"] == []
    assert set(report["gates"]) == set(harness.REGISTRY)
    assert "sound" in capsys.readouterr().out


def test_the_cli_refuses_to_score_when_the_self_check_fails(tmp_path):
    root = _snapshot(tmp_path / "snap", gates={**CLEAN, "sound": False})
    oracle = tmp_path / "oracle.json"
    oracle.write_text(json.dumps({"cases": []}), encoding="utf-8")
    assert harness.main([str(root), str(oracle)]) == 2
