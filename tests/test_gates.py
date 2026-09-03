from __future__ import annotations

from mu_spec.gates import ORPHAN, UNSERVED, admission_gates
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse


def _entry(ident: str, derives_from: str = "", **kw) -> Entry:
    return Entry(
        id=parse(ident),
        derives_from=tuple(parse(d) for d in derives_from.split() if d),
        body=f"# {ident} title\n",
        **kw,
    )


def _kinds(findings):
    return sorted((f.kind, str(f.id)) for f in findings)


# -- a clean graph ----------------------------------------------------------


def test_a_well_formed_graph_produces_no_findings():
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _entry("A·01", "B·01"),
            _entry("S·01", "A·01"),
        ]
    )
    assert admission_gates(graph) == []


def test_intent_entries_are_never_orphans():
    """Intent is the top layer -- it derives from nothing by definition, and
    flagging it would make every graph permanently red. B·01 is reported
    unserved here, which is a completeness finding, not a soundness one:
    nothing has been derived from it yet."""
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01")])
    assert [f for f in admission_gates(graph) if f.kind == ORPHAN] == []


def test_spec_entries_are_never_unserved():
    """Spec is the bottom layer of this graph; what serves it is code, which
    is tracked by module backlinks rather than by entries here."""
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01"), _entry("S·01", "B·01")])
    assert admission_gates(graph) == []


# -- orphans: something below tracing to nothing above ----------------------


def test_an_entry_with_no_derives_from_is_an_orphan():
    graph = Graph([_entry("I·01"), _entry("B·01")])
    assert _kinds(admission_gates(graph)) == [
        (ORPHAN, "B·01"),
        (UNSERVED, "B·01"),
        (UNSERVED, "I·01"),
    ]


def test_an_entry_deriving_from_a_missing_identifier_is_an_orphan():
    """A dangling edge is exactly as broken as no edge at all, and much
    easier to miss by eye."""
    graph = Graph([_entry("I·01"), _entry("B·01", "I·99")])
    findings = [f for f in admission_gates(graph) if f.kind == ORPHAN]
    assert [str(f.id) for f in findings] == ["B·01"]
    assert "I·99" in findings[0].detail


def test_an_entry_deriving_downward_is_an_orphan():
    """Derives-from must run toward intent. An architecture entry claiming to
    derive from a spec entry has the pipeline upside down."""
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01"), _entry("A·01", "S·01"), _entry("S·01", "B·01")])
    findings = [f for f in admission_gates(graph) if f.kind == ORPHAN]
    assert [str(f.id) for f in findings] == ["A·01"]
    assert "not upward" in findings[0].detail


def test_an_entry_deriving_sideways_within_its_own_layer_is_an_orphan():
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01"), _entry("B·02", "B·01")])
    findings = [f for f in admission_gates(graph) if f.kind == ORPHAN]
    assert [str(f.id) for f in findings] == ["B·02"]


def test_one_valid_parent_is_enough_but_a_bad_edge_is_still_reported():
    """An entry with a good parent and a dangling one is not an orphan -- it
    traces to something above -- but the dangling edge is still a defect
    someone has to fix, so it must not vanish."""
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01 I·99")])
    findings = [f for f in admission_gates(graph) if f.kind == ORPHAN]
    assert [str(f.id) for f in findings] == ["B·01"]


# -- unserved: something above with nothing below ---------------------------


def test_an_entry_nothing_derives_from_is_unserved():
    """A requirement no lower layer serves is a requirement nobody built."""
    graph = Graph([_entry("I·01"), _entry("I·02"), _entry("B·01", "I·01")])
    findings = [f for f in admission_gates(graph) if f.kind == UNSERVED]
    # I·02 has nothing under it at all; B·01 has not been carried down to
    # architecture yet. Both are real outstanding work.
    assert [str(f.id) for f in findings] == ["I·02", "B·01"]


def test_being_served_by_any_lower_layer_counts_not_just_the_adjacent_one():
    """Skipping a layer is legal, so an intent entry served directly by a
    spec entry is served."""
    graph = Graph([_entry("I·01"), _entry("S·01", "I·01")])
    assert admission_gates(graph) == []


# -- superseded entries do not participate ----------------------------------


def test_a_superseded_entry_is_not_reported_as_unserved():
    """It is retired, not unmet. Reporting it would make every amendment
    permanently dirty the gate."""
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _entry("B·02", "I·01", supersedes=parse("B·01")),
        ]
    )
    unserved_ids = [str(f.id) for f in admission_gates(graph) if f.kind == UNSERVED]
    assert "B·01" not in unserved_ids
    assert unserved_ids == ["B·02"]


def test_deriving_from_a_superseded_entry_is_an_orphan():
    """The replacement carries the identifier that should be referenced. An
    edge left pointing at the retired one is a stale reference, which is the
    exact rot the append-only rule exists to make visible."""
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _entry("B·02", "I·01", supersedes=parse("B·01")),
            _entry("A·01", "B·01"),
        ]
    )
    findings = [f for f in admission_gates(graph) if f.kind == ORPHAN]
    assert [str(f.id) for f in findings] == ["A·01"]
    assert "superseded" in findings[0].detail


# -- reporting shape --------------------------------------------------------


def test_findings_are_sorted_in_spine_order():
    """The human sees only failures, so the list is the entire report. Spine
    order means it reads top-down like the graph does."""
    graph = Graph([_entry("S·01"), _entry("B·01")])
    assert _kinds(admission_gates(graph)) == [
        (ORPHAN, "B·01"),
        (ORPHAN, "S·01"),
        (UNSERVED, "B·01"),
    ]
    assert [str(f.id) for f in admission_gates(graph)] == ["B·01", "B·01", "S·01"]
