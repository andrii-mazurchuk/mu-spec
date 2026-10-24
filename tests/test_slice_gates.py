from __future__ import annotations

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.slice_gates import DEPENDENCY_CYCLE, OVERLAPPING_MEMBERSHIP, slice_gates
from mu_spec.storage import Manifest, Slice


def _manifest(**slices) -> Manifest:
    """slices: name=(ids, type). Membership is a set, so the ids are given as
    a plain string of identifiers."""
    return Manifest(
        project="m",
        slices={
            name: Slice(
                name=name,
                members={parse(i) for i in ids.split()},
                type=kind,
            )
            for name, (ids, kind) in slices.items()
        },
    )


def _entry(ident: str, depends_on: str = "") -> Entry:
    return Entry(
        id=parse(ident),
        depends_on=tuple(parse(d) for d in depends_on.split() if d),
        title=ident,
    )


def _kinds(findings):
    return sorted((f.kind, f.slice) for f in findings)


# -- cycles ------------------------------------------------------------------


def test_a_one_way_dependency_is_clean():
    manifest = _manifest(a=("S-01", "slice"), b=("S-02", "slice"))
    graph = Graph([_entry("S-01", "S-02"), _entry("S-02")])
    assert slice_gates(manifest, graph) == []


def test_a_mutual_dependency_between_two_slices_is_a_cycle():
    """Illegal, not merely awkward. Two slices that need each other cannot be
    ordered, and a slice is the unit of work -- so neither can start."""
    manifest = _manifest(a=("S-01", "slice"), b=("S-02", "slice"))
    graph = Graph([_entry("S-01", "S-02"), _entry("S-02", "S-01")])
    findings = slice_gates(manifest, graph)
    assert _kinds(findings) == [(DEPENDENCY_CYCLE, "a")]
    assert "a -> b -> a" in findings[0].detail


def test_a_longer_cycle_is_caught_too():
    manifest = _manifest(
        a=("S-01", "slice"), b=("S-02", "slice"), c=("S-03", "slice")
    )
    graph = Graph(
        [_entry("S-01", "S-02"), _entry("S-02", "S-03"), _entry("S-03", "S-01")]
    )
    findings = slice_gates(manifest, graph)
    assert [f.kind for f in findings] == [DEPENDENCY_CYCLE]
    assert "a -> b -> c -> a" in findings[0].detail


def test_one_cycle_is_reported_once_not_once_per_member():
    manifest = _manifest(a=("S-01", "slice"), b=("S-02", "slice"))
    graph = Graph([_entry("S-01", "S-02"), _entry("S-02", "S-01")])
    assert len(slice_gates(manifest, graph)) == 1


def test_two_slices_depending_on_a_third_is_not_a_cycle():
    """A foundational slice. Many things depending on one thing is ordinary,
    and it is how an ambient concern is expressed."""
    manifest = _manifest(
        a=("S-01", "slice"), b=("S-02", "slice"), shared=("S-03", "slice")
    )
    graph = Graph(
        [_entry("S-01", "S-03"), _entry("S-02", "S-03"), _entry("S-03")]
    )
    assert slice_gates(manifest, graph) == []


def test_a_diamond_is_not_a_cycle():
    manifest = _manifest(
        top=("S-01", "slice"),
        left=("S-02", "slice"),
        right=("S-03", "slice"),
        base=("S-04", "slice"),
    )
    graph = Graph(
        [
            _entry("S-01", "S-02 S-03"),
            _entry("S-02", "S-04"),
            _entry("S-03", "S-04"),
            _entry("S-04"),
        ]
    )
    assert slice_gates(manifest, graph) == []


def test_an_empty_project_is_clean():
    assert slice_gates(Manifest(project="m"), Graph([])) == []


def test_a_legacy_cross_cutting_type_is_an_ordinary_slice():
    """D6 removed the type. A manifest still saying cross_cutting gates like
    any slice -- reaching into a feature slice is a plain dependency -- and a
    stored emission imposes nothing."""
    manifest = _manifest(a=("S-01", "slice"), audit=("S-02", "cross_cutting"))
    graph = Graph([
        _entry("S-01"),
        _entry("S-02", "S-01"),
        Entry(id=parse("S-03"), emits_into=(parse("S-02"),), title="S-03"),
    ])
    assert slice_gates(manifest, graph) == []
    assert manifest.dependency_graph(graph) == {"a": (), "audit": ("a",)}


# -- one entry, one slice ----------------------------------------------------


def test_an_entry_in_two_slices_is_refused():
    """Slices define write ownership, so overlapping membership is no
    ownership: two work packages would both hand the same entry out as
    editable, and the audit would pass for both -- which makes it blind to
    the one thing that broke."""
    manifest = _manifest(a=("S-01 S-02", "slice"), b=("S-02", "slice"))
    findings = slice_gates(manifest, Graph([_entry("S-01"), _entry("S-02")]))
    assert [f.kind for f in findings] == [OVERLAPPING_MEMBERSHIP]
    assert "S-02" in findings[0].detail
    assert "a, b" in findings[0].detail


def test_distinct_membership_is_clean():
    manifest = _manifest(a=("S-01", "slice"), b=("S-02", "slice"))
    assert slice_gates(manifest, Graph([_entry("S-01"), _entry("S-02")])) == []

