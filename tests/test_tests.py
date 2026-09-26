"""The test contract: a scenario is an entry, and ordering follows from it.

Covers the four properties DESIGN 4b rests on, each of which is the reason a
piece of code exists rather than a restatement of it:

- a test derives from exactly one spec entry, and from nothing else ever
- spec stays the bottom of the derivation chain, so an untested project is
  incomplete and never unsound
- a test unit is a root, structurally, so the ordering rule cannot deadlock
- the implementation unit follows the test unit, fanning out and merging
  nothing
"""

from __future__ import annotations

import pytest

from mu_spec.gates import ORPHAN, UNSERVED, UNTESTED, admission_gates
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import ALL_LAYERS, InvalidIdentifier, parse, sort_key
from mu_spec.storage import Manifest, ProjectStore, Slice
from mu_spec.units import project

ARCH = parse("A·01")


def _spec(ident: str, depends_on: str = "") -> Entry:
    return Entry(
        id=parse(ident),
        derives_from=(ARCH,),
        depends_on=tuple(parse(d) for d in depends_on.split() if d),
        title=ident,
    )


def _test(ident: str, derives_from: str, purpose: str = "") -> Entry:
    return Entry(
        id=parse(ident),
        derives_from=tuple(parse(d) for d in derives_from.split() if d),
        title=ident,
        purpose=purpose,
    )


def _graph(*entries: Entry) -> Graph:
    return Graph([Entry(id=ARCH, title="arch"), *entries])


def _manifest(modules: dict[str, str], slices: dict[str, str] | None = None):
    manifest = Manifest(project="m")
    for path, ids in modules.items():
        manifest.modules[path] = {parse(i) for i in ids.split()}
    for name, ids in (slices or {}).items():
        manifest.slices[name] = Slice(
            name=name, members={parse(i) for i in ids.split()}
        )
    return manifest


def _kinds(findings):
    return sorted((f.kind, str(f.id)) for f in findings)


def _seeded(tmp_path) -> ProjectStore:
    """A project holding one architecture entry, one spec entry under a
    slice, and one scenario judging it."""
    store = ProjectStore(tmp_path)
    store.create_project("p")
    store.append("p", [Entry(id=ARCH, title="arch")], slice_name="billing")
    store.append("p", [_spec("S·01")], slice_name="billing")
    store.append("p", [_test("T·01", "S·01", purpose="the empty cart case")])
    return store


# -- the identifier ----------------------------------------------------------


def test_a_test_identifier_parses_and_sorts_last():
    """T is a layer, so it parses like any other -- and it sits past spec, so
    a spine reads the chain in order and then the scenarios."""
    assert "T" in ALL_LAYERS
    assert str(parse("T·07")) == "T·07"
    ordered = sorted(
        (parse(i) for i in ("T·01", "S·01", "I·01", "B·01", "A·01")), key=sort_key
    )
    assert [i.layer for i in ordered] == ["I", "B", "A", "S", "T"]


def test_a_test_is_not_part_of_the_derivation_chain():
    """The load-bearing exclusion. If T sat inside the ordered chain, spec
    would stop being the bottom of it and every untested spec entry would be
    reported UNSERVED -- a completeness finding about propagation, which is a
    different question with a different remedy."""
    graph = _graph(_spec("S·01"), _test("T·01", "S·01"))
    # A*01 is a fixture root deriving from nothing, so it is the one expected
    # finding; the point is that neither S*01 nor T*01 contributes another.
    assert _kinds(admission_gates(graph)) == [(ORPHAN, "A·01")]

    bare = _graph(_spec("S·01"))
    assert (UNSERVED, "S·01") not in _kinds(admission_gates(bare))
    assert (UNTESTED, "S·01") in _kinds(admission_gates(bare))


def test_a_test_is_never_reported_unserved():
    """Nothing derives from a test, ever. Asking the completeness question of
    one would report every scenario in the project, forever."""
    graph = _graph(_spec("S·01"), _test("T·01", "S·01"))
    assert (UNSERVED, "T·01") not in _kinds(admission_gates(graph))


def test_no_layer_sits_past_a_test():
    """Which is why nothing can legally derive from one. The arithmetic gives
    it for free -- there is no rule asserting it anywhere."""
    with pytest.raises(InvalidIdentifier):
        parse("U·01")


# -- exactly one parent ------------------------------------------------------


def test_a_test_derives_from_exactly_one_spec_entry():
    """One scenario, one contract. A test citing two cannot say which one it
    falsifies when it fails, and the ordering rule would drag an unrelated
    implementation unit behind it for a scenario that never judged it."""
    two = _graph(_spec("S·01"), _spec("S·02"), _test("T·01", "S·01 S·02"))
    assert (ORPHAN, "T·01") in _kinds(admission_gates(two))

    none = _graph(_spec("S·01"), _test("T·01", ""))
    assert (ORPHAN, "T·01") in _kinds(admission_gates(none))


def test_a_test_may_not_derive_from_the_layers_above_spec():
    graph = _graph(_spec("S·01"), _test("T·01", "A·01"))
    assert (ORPHAN, "T·01") in _kinds(admission_gates(graph))


def test_a_test_carries_no_horizontal_edge():
    """What makes a test unit a root. With no outbound edge to have, a unit
    holding test modules cannot sit inside a cycle, so ordering implementation
    behind tests can never deadlock against a dependency."""
    graph = Graph(
        [
            Entry(id=ARCH, title="arch"),
            _spec("S·01"),
            Entry(
                id=parse("T·01"),
                derives_from=(parse("S·01"),),
                depends_on=(parse("T·02"),),
                title="t1",
            ),
            _test("T·02", "S·01"),
        ]
    )
    assert ("bad_dependency", "T·01") in _kinds(admission_gates(graph))


# -- coverage reports, never gates -------------------------------------------


def test_an_untested_spec_entry_is_reported_and_never_blocks():
    """Tests are written after the spec and before the code, so every spec
    entry is untested for the window between those two events. A gate here
    would refuse the project during the ordinary course of writing it."""
    projection = project(_manifest({"a.py": "S·01"}), _graph(_spec("S·01")))
    assert projection.untested == (parse("S·01"),)
    assert len(projection.units) == 1


def test_a_test_with_no_module_is_reported_and_orders_nothing():
    """Specified but not built is not yet expected to pass. Ordering the
    implementation behind it would block work on a file nobody has written."""
    projection = project(
        _manifest({"a.py": "S·01"}),
        _graph(_spec("S·01"), _test("T·01", "S·01")),
    )
    assert projection.unimplemented_tests == (parse("T·01"),)
    assert projection.untested == ()
    impl = projection.units[0]
    assert projection.edges[impl.key] == ()


# -- units -------------------------------------------------------------------


def test_a_test_module_and_an_implementation_module_are_different_units():
    """The separation DESIGN 4b.2 claims needs no enforcing. They share no
    entry, so they fall into different units -- different write sets,
    different branches, different agents. A property of the grouping."""
    projection = project(
        _manifest({"a.py": "S·01", "test_a.py": "T·01"}),
        _graph(_spec("S·01"), _test("T·01", "S·01")),
    )
    assert len(projection.units) == 2
    impl = next(u for u in projection.units if not u.tests)
    tests = next(u for u in projection.units if u.tests)
    assert impl.modules == ("a.py",)
    assert tests.modules == ("test_a.py",)
    assert set(impl.modules) & set(tests.modules) == set()


def test_the_implementation_unit_follows_the_test_unit():
    projection = project(
        _manifest({"a.py": "S·01", "test_a.py": "T·01"}),
        _graph(_spec("S·01"), _test("T·01", "S·01")),
    )
    impl = next(u for u in projection.units if not u.tests)
    tests = next(u for u in projection.units if u.tests)
    assert projection.edges[impl.key] == (tests.key,)
    assert projection.edges[tests.key] == ()


def test_a_test_unit_lands_in_wave_zero():
    """Not arranged -- a consequence of a test having no outbound edge, the
    same way a cross-cutting slice lands in wave 0 by construction."""
    projection = project(
        _manifest({"a.py": "S·01", "test_a.py": "T·01"}),
        _graph(_spec("S·01"), _test("T·01", "S·01")),
    )
    tests = next(u for u in projection.units if u.tests)
    assert tests.key in projection.schedule.waves[0]
    assert projection.schedule.unschedulable == ()


def test_a_shared_fixture_fans_out_and_merges_nothing():
    """The pairing is not one-to-one and nothing may assume it is. One test
    module implementing scenarios for two unrelated spec entries is followed
    by BOTH implementation units -- and does not glue them together, which is
    what anchoring at the entry rather than the module buys."""
    projection = project(
        _manifest(
            {"a.py": "S·01", "b.py": "S·02", "test_shared.py": "T·01 T·02"}
        ),
        _graph(
            _spec("S·01"),
            _spec("S·02"),
            _test("T·01", "S·01"),
            _test("T·02", "S·02"),
        ),
    )
    by_entry = projection.unit_of()
    a = by_entry[parse("S·01")]
    b = by_entry[parse("S·02")]
    shared = by_entry[parse("T·01")]

    assert a != b, "one test module must not merge two implementation units"
    assert by_entry[parse("T·02")] == shared
    assert projection.edges[a] == (shared,)
    assert projection.edges[b] == (shared,)


def test_a_test_unit_nothing_follows_is_reported():
    """Ordinary while tests run ahead of code. Worth seeing anyway: a test
    unit that stays unfollowed judges a contract nobody is building."""
    projection = project(
        _manifest({"test_a.py": "T·01"}),
        _graph(_spec("S·01"), _test("T·01", "S·01")),
    )
    tests = next(u for u in projection.units if u.tests)
    assert projection.unfollowed_tests == (tests.key,)
    assert projection.unimplemented == (parse("S·01"),)


def test_a_test_unit_reports_the_slice_it_tests():
    """Derived through the parent, never stored. A slice split redistributes
    spec entries and the tests follow without anything being rewritten."""
    projection = project(
        _manifest(
            {"a.py": "S·01", "test_a.py": "T·01"}, slices={"billing": "S·01"}
        ),
        _graph(_spec("S·01"), _test("T·01", "S·01")),
    )
    tests = next(u for u in projection.units if u.tests)
    assert tests.slices == ("billing",)


# -- the module map ----------------------------------------------------------


def test_a_module_may_not_claim_spec_and_test_entries_together(tmp_path):
    """The one rule holding the separation up. A mixed file merges the test
    unit and the implementation unit, the write sets stop being disjoint, and
    'the implementer may never write the tests' quietly stops being
    structural and becomes an honour-system rule."""
    store = _seeded(tmp_path)
    store.set_module("p", "a.py", ["S·01"])
    store.set_module("p", "test_a.py", ["T·01"])
    with pytest.raises(ValueError, match="both spec and test"):
        store.set_module("p", "mixed.py", ["S·01", "T·01"])


def test_a_module_may_not_claim_a_layer_above_spec(tmp_path):
    store = _seeded(tmp_path)
    with pytest.raises(ValueError, match="architecture"):
        store.set_module("p", "a.py", ["A·01"])


# -- storage -----------------------------------------------------------------


def test_tests_are_stored_flat_and_join_no_slice(tmp_path):
    """A test's column is its parent's, resolved on demand. Recording it
    would be the second copy that goes stale the first time a slice splits."""
    store = _seeded(tmp_path)

    assert (tmp_path / "p" / "tests.jsonl").exists()
    manifest = store.load_manifest("p")
    assert parse("T·01") not in manifest.slices["billing"].members

    reloaded = store.load_graph("p").get(parse("T·01"))
    assert reloaded.purpose == "the empty cart case"
    assert reloaded.derives_from == (parse("S·01"),)


def test_a_project_without_tests_loads_unchanged(tmp_path):
    """Every behaviour here is a no-op until the first scenario exists. A
    project written before tests did must keep opening."""
    store = ProjectStore(tmp_path)
    store.create_project("p")
    (tmp_path / "p" / "tests.jsonl").unlink()
    store.append("p", [Entry(id=ARCH, title="arch")], slice_name="billing")

    assert [str(e.id) for e in store.load_graph("p").entries()] == ["A·01"]


# -- the module map's projections --------------------------------------------


def test_a_module_reaches_its_slices_most_represented_first(tmp_path):
    """A file may serve more than one slice and real ones do. Ordering by
    how many of its entries come from each lets a caller needing a single
    value take the first, without this unit asserting one slice as fact."""
    from mu_spec.units import module_slices

    manifest = _manifest(
        {"wide.py": "S·01 S·02 S·03"},
        slices={"capture": "S·01", "platform": "S·02 S·03"},
    )
    graph = _graph(_spec("S·01"), _spec("S·02"), _spec("S·03"))
    assert module_slices(manifest, graph, "wide.py") == ("platform", "capture")


def test_a_tie_between_slices_stays_a_tie(tmp_path):
    """Breaking by name keeps it deterministic. It does not pretend the
    plurality decided anything -- the worst straddler in the sample data is
    a dead four-four, so a winner-takes-all rule would decide nothing in
    exactly the case it exists for."""
    from mu_spec.units import module_slices

    manifest = _manifest(
        {"even.py": "S·01 S·02"},
        slices={"capture": "S·01", "platform": "S·02"},
    )
    graph = _graph(_spec("S·01"), _spec("S·02"))
    assert module_slices(manifest, graph, "even.py") == ("capture", "platform")


def test_a_test_module_reaches_the_slice_of_what_it_judges(tmp_path):
    """It has no membership of its own, and asking the manifest directly
    used to answer null -- which is what made the same unit give two
    different answers to one question."""
    from mu_spec.units import module_slices

    manifest = _manifest(
        {"tests/t.py": "T·01"}, slices={"capture": "S·01"}
    )
    graph = _graph(_spec("S·01"), _test("T·01", "S·01"))
    assert module_slices(manifest, graph, "tests/t.py") == ("capture",)


def test_the_link_between_test_and_code_is_computed_both_ways():
    """Stored once it needs an inverse; stored twice it needs integrity
    enforcement, and the enforcement is the cost of having made the copies
    rather than a guard against anything. Computed, changing `implements`
    moves both directions at once because there is no second thing."""
    from mu_spec.units import covers, covered_by

    manifest = _manifest({"db.py": "S·01", "tests/test_db.py": "T·01"})
    graph = _graph(_spec("S·01"), _test("T·01", "S·01"))

    assert covers(manifest, graph, "tests/test_db.py") == ("db.py",)
    assert covered_by(manifest, graph, "db.py") == ("tests/test_db.py",)


def test_a_test_module_covering_nothing_is_visible_not_an_error():
    """Judging a contract nobody built. Ordinary while tests run ahead of
    the code -- which is the order this design asks for -- so it is reported
    and never refused."""
    from mu_spec.units import covers

    manifest = _manifest({"tests/test_db.py": "T·01"})
    graph = _graph(_spec("S·01"), _test("T·01", "S·01"))
    assert covers(manifest, graph, "tests/test_db.py") == ()


def test_a_shared_fixture_covers_every_file_it_judges():
    """The pairing is not one-to-one in this direction either."""
    from mu_spec.units import covers

    manifest = _manifest(
        {"a.py": "S·01", "b.py": "S·02",
         "tests/shared.py": "T·01 T·02"}
    )
    graph = _graph(
        _spec("S·01"), _spec("S·02"),
        _test("T·01", "S·01"), _test("T·02", "S·02"),
    )
    assert covers(manifest, graph, "tests/shared.py") == ("a.py", "b.py")


# -- what a cut records ------------------------------------------------------


def test_a_cut_records_what_verification_looked_like(tmp_path):
    """The one question a stored cut exists to answer is what was true when
    work went out, and on the half concerning tests it was silent: the live
    graph has moved on by the time anyone asks."""
    from mu_spec.units import append_cut, current_cut, project

    manifest = _manifest({"a.py": "S·01", "tests/t.py": "T·02"})
    graph = _graph(
        _spec("S·01"), _spec("S·02"),
        _test("T·01", "S·01"), _test("T·02", "S·02"),
    )
    path = tmp_path / "units.jsonl"
    proj = project(manifest, graph)
    append_cut(path, proj, now_fn=lambda: 1.0, note="n")

    back = current_cut(path)
    assert back.untested == proj.untested
    assert back.unimplemented_tests == proj.unimplemented_tests
    assert back.unfollowed_tests == proj.unfollowed_tests
    assert "untested" in back.to_json()


def test_a_cut_records_an_order_it_could_not_draw(tmp_path):
    """A depends_on target in no unit. The edge is real and the projection
    cannot draw it, so the unit that should have waited does not visibly
    wait -- and after the graph moves on, nobody reconstructs which order
    was missing at the moment work went out."""
    from mu_spec.units import append_cut, current_cut, project

    manifest = _manifest({"a.py": "S·01"})
    graph = _graph(_spec("S·01", depends_on="S·09"), _spec("S·09"))
    path = tmp_path / "units.jsonl"
    proj = project(manifest, graph)
    assert proj.dangling == (parse("S·09"),)

    append_cut(path, proj, now_fn=lambda: 1.0)
    assert current_cut(path).dangling == (parse("S·09"),)


def test_a_cut_written_before_this_reloads_as_unsaid(tmp_path):
    """Not as zero. A log line that never carried the field is silent about
    it, and reading silence as "nothing was untested" would invent a fact
    about a cut nobody recorded one for."""
    from mu_spec.units import read_cuts

    path = tmp_path / "units.jsonl"
    path.write_text(
        '{"seq": 1, "at": 0.0, "note": "", "members": [], "units": {}, '
        '"edges": {}, "unimplemented": [], "stale_modules": []}\n',
        encoding="utf-8",
    )
    cut = read_cuts(path)[0]
    assert cut.untested == ()
    assert cut.unimplemented_tests == ()
    assert cut.unfollowed_tests == ()
