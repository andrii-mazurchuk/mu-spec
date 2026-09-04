from __future__ import annotations

from mu_spec.gates import BAD_DEPENDENCY, ORPHAN, UNSERVED, admission_gates
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
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _entry("A·01", "B·01"),
            _entry("S·01", "A·01"),
        ]
    )
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
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _entry("A·01", "S·01"),
            _entry("S·01", "A·01"),
        ]
    )
    findings = [f for f in admission_gates(graph) if f.kind == ORPHAN]
    assert [str(f.id) for f in findings] == ["A·01"]
    assert "directly above" in findings[0].detail


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


def test_serving_must_come_from_the_adjacent_layer():
    """A spec entry cannot serve intent directly. The edge is illegal, so the
    spec entry is an orphan and the intent entry is still unserved -- there is
    no way to satisfy a requirement by jumping the layers that explain how."""
    graph = Graph([_entry("I·01"), _entry("S·01", "I·01")])
    assert _kinds(admission_gates(graph)) == [
        (ORPHAN, "S·01"),
        (UNSERVED, "I·01"),
    ]


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


# -- same-layer dependency edges --------------------------------------------


def _dep(ident: str, derives_from: str = "", depends_on: str = "") -> Entry:
    return Entry(
        id=parse(ident),
        derives_from=tuple(parse(d) for d in derives_from.split() if d),
        depends_on=tuple(parse(d) for d in depends_on.split() if d),
        title=ident,
    )


def test_a_valid_same_layer_dependency_is_clean():
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _dep("A·01", "B·01"),
            _dep("A·02", "B·01", depends_on="A·01"),
            _entry("S·01", "A·01"),
            _entry("S·02", "A·02"),
        ]
    )
    assert admission_gates(graph) == []


def test_depending_on_another_layer_is_a_bad_dependency():
    """depends_on is horizontal by definition. An architecture entry that
    depends on a behaviour entry is claiming a derivation, and it should have
    said so with derives_from where the orphan gate can see it."""
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01"), _dep("A·01", "B·01", "B·01")])
    findings = [f for f in admission_gates(graph) if f.kind == BAD_DEPENDENCY]
    assert [str(f.id) for f in findings] == ["A·01"]
    assert "same layer" in findings[0].detail


def test_depending_on_something_that_does_not_exist_is_a_bad_dependency():
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01"), _dep("A·01", "B·01", "A·99")])
    findings = [f for f in admission_gates(graph) if f.kind == BAD_DEPENDENCY]
    assert "does not exist" in findings[0].detail


def test_depending_on_a_superseded_entry_is_a_bad_dependency():
    """The consumer is holding the old meaning. This is exactly the case the
    horizontal edge exists to make visible."""
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _dep("A·01", "B·01"),
            _dep("A·02", "B·01", depends_on="A·01"),
            _dep("A·03", "B·01"),
        ]
        + [Entry(id=parse("A·04"), derives_from=(parse("B·01"),), title="x", supersedes=parse("A·01"))]
    )
    findings = [f for f in admission_gates(graph) if f.kind == BAD_DEPENDENCY]
    assert [str(f.id) for f in findings] == ["A·02"]
    assert "superseded" in findings[0].detail


def test_an_entry_depending_on_itself_is_a_bad_dependency():
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01"), _dep("A·01", "B·01", "A·01")])
    findings = [f for f in admission_gates(graph) if f.kind == BAD_DEPENDENCY]
    assert "itself" in findings[0].detail
