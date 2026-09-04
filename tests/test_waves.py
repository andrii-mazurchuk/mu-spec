from __future__ import annotations

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.storage import CROSS_CUTTING, Manifest, Slice
from mu_spec.waves import schedule


def _manifest(**slices) -> Manifest:
    return Manifest(
        project="m",
        slices={
            name: Slice(
                name=name,
                members={parse(i) for i in spec[0].split()},
                type=spec[1] if len(spec) > 1 else "slice",
            )
            for name, spec in slices.items()
        },
    )


def _entry(ident: str, depends_on: str = "", emits_into: str = "") -> Entry:
    return Entry(
        id=parse(ident),
        depends_on=tuple(parse(d) for d in depends_on.split() if d),
        emits_into=tuple(parse(d) for d in emits_into.split() if d),
        title=ident,
    )


def test_slices_with_no_dependencies_share_wave_zero():
    manifest = _manifest(a=("S·01",), b=("S·02",))
    sched = schedule(manifest, Graph([_entry("S·01"), _entry("S·02")]))
    assert sched.waves == (("a", "b"),)


def test_a_chain_produces_one_slice_per_wave():
    manifest = _manifest(a=("S·01",), b=("S·02",), c=("S·03",))
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02", "S·03"), _entry("S·03")])
    sched = schedule(manifest, graph)
    assert sched.waves == (("c",), ("b",), ("a",))
    assert sched.chain is True


def test_the_wave_is_the_longest_path_not_the_shortest():
    """`top` depends on `mid` and on `base` directly. The direct edge would
    put it in wave 1, where it would sit alongside `mid` -- which it depends
    on. The longest path is what keeps it strictly after everything it needs.
    """
    manifest = _manifest(top=("S·01",), mid=("S·02",), base=("S·03",))
    graph = Graph(
        [
            _entry("S·01", "S·02 S·03"),
            _entry("S·02", "S·03"),
            _entry("S·03"),
        ]
    )
    sched = schedule(manifest, graph)
    assert sched.waves == (("base",), ("mid",), ("top",))


def test_a_diamond_puts_the_two_arms_in_one_wave():
    manifest = _manifest(
        top=("S·01",), left=("S·02",), right=("S·03",), base=("S·04",)
    )
    graph = Graph(
        [
            _entry("S·01", "S·02 S·03"),
            _entry("S·02", "S·04"),
            _entry("S·03", "S·04"),
            _entry("S·04"),
        ]
    )
    sched = schedule(manifest, graph)
    assert sched.waves == (("base",), ("left", "right"), ("top",))
    assert sched.chain is False


def test_no_two_slices_in_one_wave_have_an_edge_between_them():
    """The property the whole scheme rests on: agents in a wave never need to
    talk to each other, structurally rather than usually."""
    manifest = _manifest(
        top=("S·01",),
        left=("S·02",),
        right=("S·03",),
        base=("S·04",),
        aside=("S·05",),
    )
    graph = Graph(
        [
            _entry("S·01", "S·02 S·03"),
            _entry("S·02", "S·04"),
            _entry("S·03", "S·04"),
            _entry("S·04"),
            _entry("S·05", "S·04"),
        ]
    )
    edges = manifest.dependency_graph(graph)
    for wave in schedule(manifest, graph).waves:
        for name in wave:
            assert not set(edges[name]) & set(wave)


def test_a_cross_cutting_slice_lands_in_wave_zero_by_construction():
    """Nothing arranges this. The edge rules leave a concern no outbound
    dependency to have, so it has no path to anything and its longest path is
    zero."""
    manifest = _manifest(
        a=("S·01",), b=("S·02",), audit=("S·03", CROSS_CUTTING)
    )
    graph = Graph(
        [
            _entry("S·01", depends_on="S·02", emits_into="S·03"),
            _entry("S·02", emits_into="S·03"),
            _entry("S·03"),
        ]
    )
    sched = schedule(manifest, graph)
    assert sched.wave_of()["audit"] == 0
    assert sched.waves == (("audit", "b"), ("a",))


def test_a_cycle_comes_back_unschedulable_instead_of_hanging():
    """The gates refuse a cycle, but a gate can only report on a structure it
    can walk -- so this has to terminate on a broken graph."""
    manifest = _manifest(a=("S·01",), b=("S·02",), c=("S·03",))
    graph = Graph(
        [_entry("S·01", "S·02"), _entry("S·02", "S·01"), _entry("S·03")]
    )
    sched = schedule(manifest, graph)
    assert sched.unschedulable == ("a", "b")
    assert sched.waves == (("c",),)


def test_an_empty_project_schedules_nothing():
    sched = schedule(Manifest(project="m"), Graph([]))
    assert sched.waves == ()
    assert sched.unschedulable == ()
    assert sched.chain is False


def test_wave_of_maps_every_scheduled_slice():
    manifest = _manifest(a=("S·01",), b=("S·02",))
    graph = Graph([_entry("S·01", "S·02"), _entry("S·02")])
    assert schedule(manifest, graph).wave_of() == {"b": 0, "a": 1}
