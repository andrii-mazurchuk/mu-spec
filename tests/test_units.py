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


def test_two_entries_living_in_one_file_are_one_unit():
    """An entry is too fine a grain to be a branch. Two entries in one file
    would each claim the right to write it, and two branches writing one file
    is the merge conflict the whole projection exists to make impossible."""
    manifest = _manifest({"a.py": "S·01 S·02"})
    projection = project(manifest, _graph(_entry("S·01"), _entry("S·02")))

    assert len(projection.units) == 1
    assert projection.units[0].entries == (parse("S·01"), parse("S·02"))
    assert projection.units[0].modules == ("a.py",)


def test_one_entry_spanning_two_files_is_one_unit():
    """A module is too fine a grain in the other direction. Real entries span
    files -- config, manifest and code land together -- so splitting per file
    would hand half an entry's write set to one branch and half to another."""
    manifest = _manifest({"a.py": "S·01", "b.py": "S·01"})
    projection = project(manifest, _graph(_entry("S·01")))

    assert len(projection.units) == 1
    assert projection.units[0].modules == ("a.py", "b.py")
    assert projection.units[0].entries == (parse("S·01"),)


def test_no_module_path_appears_in_two_units():
    """The property everything else rests on. Two units may be worked in the
    same wave, at the same time, on different branches -- and they can only
    be merged blind if no file is in both write sets."""
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

    seen: dict[str, str] = {}
    for unit in projection.units:
        for path in unit.modules:
            assert path not in seen, f"{path} in {seen[path]} and {unit.key}"
            seen[path] = unit.key
    assert len(projection.units) == 3


def test_files_joined_through_a_shared_entry_join_transitively():
    """Connectivity is transitive or it is not disjointness. If a.py and c.py
    landed in different units because they share nothing directly, b.py would
    have to be in both -- and then two branches write b.py."""
    manifest = _manifest({"a.py": "S·01", "b.py": "S·01 S·02", "c.py": "S·02"})
    projection = project(manifest, _graph(_entry("S·01"), _entry("S·02")))

    assert len(projection.units) == 1
    assert projection.units[0].modules == ("a.py", "b.py", "c.py")


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


def test_a_dependency_resolved_inside_one_unit_imposes_no_order():
    """Both ends are built by the same branch, so there is nothing to wait
    for. Keeping it as a self-edge would make every unit look blocked on
    itself and strand the whole schedule."""
    manifest = _manifest({"a.py": "S·01 S·02"})
    projection = project(
        manifest, _graph(_entry("S·01"), _entry("S·02", depends_on="S·01"))
    )

    (unit,) = projection.units
    assert projection.edges == {unit.key: ()}
    assert projection.schedule.waves == ((unit.key,),)


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


def test_two_files_depending_on_each_other_contract_into_one_unit():
    """Grouping entries into files can create a cycle the entry graph does
    not have. Files that need each other cannot be built separately, so they
    are one piece of work -- refusing here would refuse a legal spec."""
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

    (unit,) = projection.units
    assert unit.cycle is True
    assert unit.entries == tuple(parse(f"S·0{n}") for n in (1, 2, 3, 4))
    assert unit.modules == ("a.py", "b.py")
    assert projection.schedule.unschedulable == ()


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


def test_the_key_is_the_entry_set_and_nothing_else():
    """Nothing allocates a unit key, so two projections of one graph agree
    without consulting each other -- which is the only thing that lets a cut
    be compared with a later one at all."""
    ids = [parse(i) for i in ("S·01", "S·02", "S·03")]
    assert unit_key(ids) == unit_key(reversed(ids))
    assert unit_key(ids) != unit_key(ids[:2])


def test_cross_slice_units_are_flagged_and_not_refused():
    """A file straddling a slice boundary is a fact about the module map, not
    an illegal state. Refusing it would block work on a graph that is
    perfectly derivable; reporting it lets a person decide."""
    manifest = _manifest(
        {"a.py": "S·01 S·02"}, {"one": "S·01", "two": "S·02"}
    )
    projection = project(manifest, _graph(_entry("S·01"), _entry("S·02")))

    (unit,) = projection.units
    assert unit.cross_slice is True
    assert unit.slices == ("one", "two")


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
