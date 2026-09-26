from __future__ import annotations

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.storage import Manifest, Slice
from mu_spec.units import project, unit_key

ARCH = parse("A·01")


def _entry(ident: str, depends_on: str = "", emits_into: str = "") -> Entry:
    return Entry(
        id=parse(ident),
        derives_from=(ARCH,),
        depends_on=tuple(parse(d) for d in depends_on.split() if d),
        emits_into=tuple(parse(d) for d in emits_into.split() if d),
        title=ident,
    )


def _graph(*entries: Entry) -> Graph:
    return Graph([Entry(id=ARCH, title="arch"), *entries])


def _manifest(modules: dict[str, str], slices: dict[str, str] | None = None) -> Manifest:
    """modules: path -> the identifiers it implements, space separated."""
    manifest = Manifest(project="m")
    for path, ids in modules.items():
        manifest.modules[path] = {parse(i) for i in ids.split()}
    for name, ids in (slices or {}).items():
        manifest.slices[name] = Slice(
            name=name, members={parse(i) for i in ids.split()}
        )
    return manifest


# -- the grain ---------------------------------------------------------------


def test_two_entries_in_one_file_are_two_units_that_overlap():
    """One ticket is one contract. Two entries in one file are two pieces of
    work, and the shared file is reported as overlap rather than grouped away
    -- grouping is what made a unit's size a property of the worst file in
    the project instead of of the contract being built."""
    manifest = _manifest({"a.py": "S·01 S·02"})
    projection = project(manifest, _graph(_entry("S·01"), _entry("S·02")))

    one, two = projection.units
    assert (one.key, two.key) == ("S·01", "S·02")
    assert one.entries == (parse("S·01"),) and two.entries == (parse("S·02"),)
    assert one.modules == two.modules == ("a.py",)
    assert projection.overlap == {"S·01": ("S·02",), "S·02": ("S·01",)}
    # Overlap is not an order. Neither waits for the other.
    assert projection.edges == {"S·01": (), "S·02": ()}


def test_one_entry_spanning_two_files_is_one_unit():
    """A module is too fine a grain in the other direction. Real entries span
    files -- config, manifest and code land together -- so splitting per file
    would hand half an entry's write set to one branch and half to another."""
    manifest = _manifest({"a.py": "S·01", "b.py": "S·01"})
    projection = project(manifest, _graph(_entry("S·01")))

    assert len(projection.units) == 1
    assert projection.units[0].modules == ("a.py", "b.py")
    assert projection.units[0].entries == (parse("S·01"),)


def test_overlap_is_symmetric_and_names_every_unit():
    """What replaced disjointness. A file in two write sets is normal; what a
    consumer needs is to know which pairs it may not dispatch at once. Every
    unit is a key even when it overlaps nothing, so an absent entry never has
    to be read as "not computed"."""
    manifest = _manifest(
        {
            "a.py": "S·01 S·02",
            "b.py": "S·02",
            "c.py": "S·03",
            "d.py": "S·04",
            "e.py": "S·04 S·05",
        }
    )
    projection = project(
        manifest,
        _graph(*(_entry(f"S·0{n}") for n in range(1, 6))),
    )

    assert len(projection.units) == 5
    assert projection.overlap == {
        "S·01": ("S·02",),
        "S·02": ("S·01",),
        "S·03": (),
        "S·04": ("S·05",),
        "S·05": ("S·04",),
    }
    for key, others in projection.overlap.items():
        for other in others:
            assert key in projection.overlap[other], "overlap must be symmetric"


def test_sharing_a_file_does_not_drag_in_what_that_file_also_implements():
    """The failure that bounded the old grain. Under connected components
    a.py, b.py and c.py collapsed into one unit because b.py bridged them,
    and a unit's size became a property of the most overloaded file rather
    than of the contract. Anchoring stops the propagation dead."""
    manifest = _manifest({"a.py": "S·01", "b.py": "S·01 S·02", "c.py": "S·02"})
    projection = project(manifest, _graph(_entry("S·01"), _entry("S·02")))

    one, two = projection.units
    assert one.modules == ("a.py", "b.py")
    assert two.modules == ("b.py", "c.py")
    assert one.entries == (parse("S·01"),) and two.entries == (parse("S·02"),)
    assert projection.overlap["S·01"] == ("S·02",)


# -- order -------------------------------------------------------------------


def test_a_dependency_between_units_is_an_edge_and_a_later_wave():
    """Disjoint files is not independence. A unit whose entries need another
    unit's has to wait, and the edge is what a consumer honours."""
    manifest = _manifest({"a.py": "S·01", "b.py": "S·02"})
    projection = project(
        manifest, _graph(_entry("S·01"), _entry("S·02", depends_on="S·01"))
    )

    first, second = projection.units
    assert first.entries == (parse("S·01"),)
    assert projection.edges == {first.key: (), second.key: (first.key,)}
    assert projection.schedule.wave_of() == {first.key: 0, second.key: 1}


def test_two_entries_in_one_file_still_order_by_their_dependency():
    """Sharing a file is not what orders these -- the dependency is. They
    overlap AND they have an edge, and it is the edge that puts them in
    different waves; the overlap would have said nothing about direction."""
    manifest = _manifest({"a.py": "S·01 S·02"})
    projection = project(
        manifest, _graph(_entry("S·01"), _entry("S·02", depends_on="S·01"))
    )

    assert projection.edges == {"S·01": (), "S·02": ("S·01",)}
    assert projection.overlap["S·02"] == ("S·01",)
    assert projection.schedule.wave_of() == {"S·01": 0, "S·02": 1}


def test_an_emission_creates_no_edge_and_no_wave_separation():
    """An emission is fire-and-forget: nothing is consumed back, so it
    imposes no order. If it did, every unit emitting into a concern would
    serialise behind it and the concern's whole reason to exist would go."""
    manifest = _manifest({"a.py": "S·01", "b.py": "S·02"})
    projection = project(
        manifest, _graph(_entry("S·01", emits_into="S·02"), _entry("S·02"))
    )

    keys = [u.key for u in projection.units]
    assert projection.edges == {k: () for k in keys}
    assert projection.schedule.waves == (tuple(sorted(keys)),)


def test_grouping_can_no_longer_manufacture_a_cycle():
    """The exact shape that used to need contracting: two files each holding
    one end of the other's dependency. Under connected components that was a
    cycle the entry graph did not have. Units anchored to spec entries are a
    relabelling of an acyclic depends_on, so there is nothing to detect."""
    manifest = _manifest({"a.py": "S·01 S·04", "b.py": "S·02 S·03"})
    projection = project(
        manifest,
        _graph(
            _entry("S·01"),
            _entry("S·02", depends_on="S·01"),
            _entry("S·03"),
            _entry("S·04", depends_on="S·03"),
        ),
    )

    assert [u.key for u in projection.units] == ["S·01", "S·02", "S·03", "S·04"]
    assert projection.schedule.unschedulable == ()
    assert projection.edges["S·02"] == ("S·01",)
    assert projection.edges["S·04"] == ("S·03",)
    # a.py and b.py are each in two write sets, and neither pairing is an order.
    assert projection.overlap["S·01"] == ("S·04",)
    assert projection.overlap["S·02"] == ("S·03",)


# -- what falls outside every unit -------------------------------------------


def test_an_entry_no_module_claims_is_unimplemented_and_in_no_unit():
    """An entry in no unit is in no piece of work. Nobody is going to build
    it, and the only way that gets noticed is if it is said out loud."""
    manifest = _manifest({"a.py": "S·01"})
    projection = project(manifest, _graph(_entry("S·01"), _entry("S·02")))

    assert projection.unimplemented == (parse("S·02"),)
    assert [u.entries for u in projection.units] == [(parse("S·01"),)]


def test_a_module_claiming_only_superseded_entries_is_stale_and_in_no_unit():
    """The manifest is checked when a module is declared and never again, so
    a module goes on naming a retired identifier. One naming only retired
    identifiers drops out of every write set -- silently, unless reported."""
    manifest = _manifest({"a.py": "S·01", "b.py": "S·02"})
    projection = project(
        manifest,
        _graph(
            _entry("S·01"),
            _entry("S·02"),
            Entry(id=parse("S·03"), derives_from=(ARCH,), supersedes=parse("S·01")),
        ),
    )

    assert projection.stale_modules == ("a.py",)
    assert [u.modules for u in projection.units] == [("b.py",)]


def test_a_module_mixing_live_and_dead_entries_is_reported_but_still_works():
    """Half-rotten is not dead. The live half is still real work with a real
    write set, so the module keeps its unit and the staleness is a report."""
    manifest = _manifest({"a.py": "S·01 S·02"})
    projection = project(
        manifest,
        _graph(
            _entry("S·01"),
            _entry("S·02"),
            Entry(id=parse("S·03"), derives_from=(ARCH,), supersedes=parse("S·01")),
        ),
    )

    assert projection.stale_modules == ("a.py",)
    (unit,) = projection.units
    assert unit.modules == ("a.py",)
    assert unit.entries == (parse("S·02"),)


def test_an_empty_module_map_describes_the_problem_instead_of_raising():
    """A person who cannot cut the work needs to see why. Raising here would
    hand them a traceback in place of the list of unclaimed entries."""
    projection = project(_manifest({}), _graph(_entry("S·01"), _entry("S·02")))

    assert projection.units == ()
    assert projection.edges == {}
    assert projection.unimplemented == (parse("S·01"), parse("S·02"))
    assert projection.schedule.waves == ()


# -- identity ----------------------------------------------------------------


def test_the_key_is_the_anchor_and_the_kind():
    """Nothing allocates a unit key, so two projections of one graph agree
    without consulting each other -- which is the only thing that lets a cut
    be compared with a later one at all. Readable, because a unit anchored to
    a spec entry has a name and a connected component did not."""
    assert unit_key(parse("S·13")) == "S·13"
    assert unit_key(parse("S·13"), tests=True) == "S·13:T"
    assert unit_key(parse("S·13")) != unit_key(parse("S·14"))


def test_a_file_straddling_a_slice_shows_up_as_overlap_across_slices():
    """A file straddling a slice boundary is a fact about the module map, not
    an illegal state. A unit can no longer straddle one -- it holds a single
    spec entry, which belongs to a single slice -- so the straddle is now
    visible where it actually lives: two units, two slices, one shared file.
    Reported, never refused."""
    manifest = _manifest(
        {"a.py": "S·01 S·02"}, {"one": "S·01", "two": "S·02"}
    )
    projection = project(manifest, _graph(_entry("S·01"), _entry("S·02")))

    one, two = projection.units
    assert one.slices == ("one",) and two.slices == ("two",)
    assert projection.overlap["S·01"] == ("S·02",)


def test_projecting_the_same_graph_twice_gives_the_same_answer():
    """A projection nobody stores has to be reproducible, or every consumer
    holding a key from an earlier run is holding a dangling reference."""
    modules = {"a.py": "S·01", "b.py": "S·02 S·03", "c.py": "S·03"}
    entries = (
        _entry("S·01"),
        _entry("S·02", depends_on="S·01"),
        _entry("S·03"),
    )
    first = project(_manifest(modules), _graph(*entries))
    second = project(_manifest(modules), _graph(*entries))

    assert [u.key for u in first.units] == [u.key for u in second.units]
    assert first.edges == second.edges

    reversed_insertion = project(
        _manifest(dict(reversed(list(modules.items())))), _graph(*entries)
    )
    assert [u.key for u in reversed_insertion.units] == [u.key for u in first.units]
    assert reversed_insertion.edges == first.edges


def test_a_unit_carries_the_size_a_consumer_batches_on():
    """How much one session can hold depends on the model driving it, which
    this unit knows nothing about -- so it reports and never compares. Body
    bytes is the only one of the three that is not already the length of a
    list, and the only one that separates a long contract from a short one."""
    manifest = _manifest({"a.py": "T·01 T·02", "b.py": "T·01"})
    spec = _entry("S·01")
    graph = _graph(
        spec,
        Entry(id=parse("T·01"), derives_from=(spec.id,), title="t1", body="a" * 40),
        Entry(id=parse("T·02"), derives_from=(spec.id,), title="t2", body="·" * 5),
    )
    (unit,) = [u for u in project(manifest, graph).units if u.tests]

    # Bytes, not characters: a body is measured as it will be read.
    assert unit.size == {"entries": 2, "modules": 2, "body_bytes": 40 + 10}
    assert unit.to_json()["size"] == unit.size


def test_the_unit_graph_cannot_cycle_however_the_files_are_grouped():
    """The property that let contraction be deleted. Units are 1:1 with spec
    entries and depends_on between spec entries is acyclic because the gates
    require it, so the unit graph is that graph relabelled. No arrangement of
    modules can manufacture a cycle, because modules no longer group."""
    manifest = _manifest(
        {"one.py": "S·01 S·03", "two.py": "S·02 S·04", "three.py": "S·01 S·04"}
    )
    projection = project(
        manifest,
        _graph(
            _entry("S·01"),
            _entry("S·02", depends_on="S·01"),
            _entry("S·03", depends_on="S·02"),
            _entry("S·04", depends_on="S·03"),
        ),
    )

    assert projection.schedule.unschedulable == ()
    assert projection.schedule.wave_of() == {
        "S·01": 0, "S·02": 1, "S·03": 2, "S·04": 3,
    }
    for key, targets in projection.edges.items():
        assert key not in targets, "a unit can never wait on itself"
