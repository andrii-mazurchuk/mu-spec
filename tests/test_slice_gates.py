from __future__ import annotations

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.slice_gates import (
    CROSS_CUTTING_OUTBOUND,
    DEPENDENCY_CYCLE,
    slice_gates,
)
from mu_spec.storage import CROSS_CUTTING, Manifest, Slice


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
    manifest = _manifest(a=("S·01", "slice"), b=("S·02", "slice"))
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02")])
    assert slice_gates(manifest, graph) == []


def test_a_mutual_dependency_between_two_slices_is_a_cycle():
    """Illegal, not merely awkward. Two slices that need each other cannot be
    ordered, and a slice is the unit of work -- so neither can start."""
    manifest = _manifest(a=("S·01", "slice"), b=("S·02", "slice"))
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02", "S·01")])
    findings = slice_gates(manifest, graph)
    assert _kinds(findings) == [(DEPENDENCY_CYCLE, "a")]
    assert "a -> b -> a" in findings[0].detail


def test_a_longer_cycle_is_caught_too():
    manifest = _manifest(
        a=("S·01", "slice"), b=("S·02", "slice"), c=("S·03", "slice")
    )
    graph = Graph(
        [_entry("S·01", "S·02"), _entry("S·02", "S·03"), _entry("S·03", "S·01")]
    )
    findings = slice_gates(manifest, graph)
    assert [f.kind for f in findings] == [DEPENDENCY_CYCLE]
    assert "a -> b -> c -> a" in findings[0].detail


def test_one_cycle_is_reported_once_not_once_per_member():
    manifest = _manifest(a=("S·01", "slice"), b=("S·02", "slice"))
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02", "S·01")])
    assert len(slice_gates(manifest, graph)) == 1


def test_two_slices_depending_on_a_third_is_not_a_cycle():
    """A foundational slice. Many things depending on one thing is ordinary,
    and it is emphatically not what makes something cross-cutting."""
    manifest = _manifest(
        a=("S·01", "slice"), b=("S·02", "slice"), shared=("S·03", "slice")
    )
    graph = Graph(
        [_entry("S·01", "S·03"), _entry("S·02", "S·03"), _entry("S·03")]
    )
    assert slice_gates(manifest, graph) == []


def test_a_diamond_is_not_a_cycle():
    manifest = _manifest(
        top=("S·01", "slice"),
        left=("S·02", "slice"),
        right=("S·03", "slice"),
        base=("S·04", "slice"),
    )
    graph = Graph(
        [
            _entry("S·01", "S·02 S·03"),
            _entry("S·02", "S·04"),
            _entry("S·03", "S·04"),
            _entry("S·04"),
        ]
    )
    assert slice_gates(manifest, graph) == []


# -- cross-cutting has no outbound edge into a feature slice -----------------


def test_a_slice_may_depend_on_a_cross_cutting_one():
    manifest = _manifest(a=("S·01", "slice"), audit=("S·02", CROSS_CUTTING))
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02")])
    assert slice_gates(manifest, graph) == []


def test_a_cross_cutting_slice_depending_on_a_feature_slice_is_refused():
    """If it has to ask a feature slice for anything, it is not
    cross-cutting -- it needs to know its caller, which is exactly what the
    classification says it must not."""
    manifest = _manifest(a=("S·01", "slice"), audit=("S·02", CROSS_CUTTING))
    graph = Graph([_entry("S·01"), _entry("S·02", "S·01")])
    findings = slice_gates(manifest, graph)
    assert _kinds(findings) == [(CROSS_CUTTING_OUTBOUND, "audit")]
    assert "a" in findings[0].detail


def test_a_cross_cutting_slice_may_depend_on_another_cross_cutting_one():
    manifest = _manifest(
        audit=("S·01", CROSS_CUTTING), telemetry=("S·02", CROSS_CUTTING)
    )
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02")])
    assert slice_gates(manifest, graph) == []


def test_cross_cutting_slices_can_still_cycle_with_each_other():
    manifest = _manifest(
        audit=("S·01", CROSS_CUTTING), telemetry=("S·02", CROSS_CUTTING)
    )
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02", "S·01")])
    assert [f.kind for f in slice_gates(manifest, graph)] == [DEPENDENCY_CYCLE]


def test_both_findings_can_appear_together():
    manifest = _manifest(a=("S·01", "slice"), audit=("S·02", CROSS_CUTTING))
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02", "S·01")])
    assert _kinds(slice_gates(manifest, graph)) == [
        (CROSS_CUTTING_OUTBOUND, "audit"),
        (DEPENDENCY_CYCLE, "a"),
    ]


def test_an_empty_project_is_clean():
    assert slice_gates(Manifest(project="m"), Graph([])) == []
