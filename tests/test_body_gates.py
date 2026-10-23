"""The gates that read entry bodies against the edges and the module map
(M2 imports, M3 file ownership), and the one-successor rule."""

from __future__ import annotations

from mu_spec import service
from mu_spec.body import legacy_imports
from mu_spec.gates import (
    BAD_IMPORT,
    NO_IMPORTS_LINE,
    TWO_SUCCESSORS,
    admission_gates,
    imports,
    two_successors,
)
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.slice_gates import (
    BAD_MODULE_IMPORT,
    SPLIT_OWNERSHIP,
    UNDECLARED_PATH,
    module_gates,
)
from mu_spec.storage import Manifest, Slice

from test_server import call, prompts, seed, store  # noqa: F401  (fixtures)


def _s(ident, body="", depends_on="", supersedes=None, preserves=False):
    return Entry(
        id=parse(ident),
        derives_from=(parse("A-01"),),
        depends_on=tuple(parse(d) for d in depends_on.split()),
        title=ident,
        body=body,
        supersedes=parse(supersedes) if supersedes else None,
        preserves_interface=preserves,
    )


def _manifest(modules=None, **slices):
    return Manifest(
        project="m",
        slices={n: Slice(name=n, members={parse(i) for i in ids.split()})
                for n, ids in slices.items()},
        modules={p: {parse(i) for i in ids.split()} for p, ids in (modules or {}).items()},
    )


def _ids(findings, kind):
    return sorted(str(f.id) for f in findings if f.kind == kind)


def _detail(findings, kind):
    return " | ".join(f.detail for f in findings if f.kind == kind)


# -- M2, id targets ----------------------------------------------------------


def test_an_imported_entry_that_is_depended_on_is_clean():
    g = Graph([_s("S-01"), _s("S-02", "Imports: S-01 Store", "S-01")])
    assert _ids(imports(g), BAD_IMPORT) == []


def test_importing_from_an_entry_without_the_edge_is_refused():
    g = Graph([_s("S-01"), _s("S-02", "Imports: S-01 Store, Record")])
    found = imports(g)
    assert _ids(found, BAD_IMPORT) == ["S-02"]
    detail = _detail(found, BAD_IMPORT)
    assert "Store, Record" in detail and "add S-01 to depends_on" in detail


def test_importing_a_retired_entry_names_its_live_successor():
    g = Graph([
        _s("S-01"), _s("S-03", supersedes="S-01"), _s("S-04", supersedes="S-03"),
        _s("S-02", "Imports: S-01 Store"),
    ])
    detail = _detail(imports(g), BAD_IMPORT)
    # The amendment path reads "X is superseded by" as blast radius.
    assert "S-01 is superseded by S-04" in detail


def test_a_retired_import_is_satisfied_by_an_edge_to_its_successor():
    g = Graph([_s("S-01"), _s("S-03", supersedes="S-01"),
               _s("S-02", "Imports: S-01 Store", "S-03")])
    assert _ids(imports(g), BAD_IMPORT) == []


def test_an_import_carried_across_an_interface_preserving_supersession_is_clean():
    """The graph reads the edge into S-01 as an edge into S-03; the import is
    read the same way, or every preserving correction would strand every
    importer -- the thing preserves_interface exists to avoid."""
    g = Graph([
        _s("S-01"), _s("S-03", supersedes="S-01", preserves=True),
        _s("S-02", "Imports: S-01 Store", "S-01"),
    ])
    assert _ids(imports(g), BAD_IMPORT) == []


def test_importing_something_that_does_not_exist_is_refused():
    g = Graph([_s("S-02", "Imports: S-09 Store")])
    assert "S-09 does not exist" in _detail(imports(g), BAD_IMPORT)


def test_an_s_without_an_imports_line_is_reported_once_the_line_is_in_use():
    g = Graph([_s("S-01"), _s("S-02", "Imports: S-01 Store", "S-01")])
    assert _ids(imports(g), NO_IMPORTS_LINE) == ["S-01"]


def test_a_project_that_never_writes_an_imports_line_reports_nothing():
    g = Graph([_s("S-01", "prose that imports `Store` (S-02)"), _s("S-02")])
    assert imports(g) == []


def test_the_legacy_reader_feeds_the_same_check():
    g = Graph([_s("S-01"), _s("S-02", "Module: `a/b.py`. Imports `Store` (S-01).")])
    found = imports(g, read=lambda e: legacy_imports(e.body) or None)
    assert _ids(found, BAD_IMPORT) == ["S-02"]


# -- M2, module targets ------------------------------------------------------


def test_a_module_import_needs_an_edge_to_an_implementer():
    g = Graph([_s("S-01"), _s("S-02", "Imports: dark.run.store Store")])
    m = _manifest({"dark/run/store.py": "S-01"})
    found = module_gates(m, g)
    assert _ids(found, BAD_MODULE_IMPORT) == ["S-02"]
    assert "S-01" in _detail(found, BAD_MODULE_IMPORT)

    g = Graph([_s("S-01"), _s("S-02", "Imports: dark.run.store Store", "S-01")])
    assert module_gates(m, g) == []


def test_a_package_import_resolves_to_its_init():
    g = Graph([_s("S-01"), _s("S-02", "Imports: dark.run Store", "S-01")])
    assert module_gates(_manifest({"dark/run/__init__.py": "S-01"}), g) == []


def test_a_module_import_under_the_code_root_that_nothing_declares_is_refused():
    g = Graph([_s("S-01"), _s("S-02", "Imports: dark.nowhere Store")])
    found = module_gates(_manifest({"dark/run/store.py": "S-01"}), g)
    assert "dark/nowhere.py" in _detail(found, BAD_MODULE_IMPORT)


def test_an_import_outside_the_code_roots_is_external_and_unchecked():
    g = Graph([_s("S-01"), _s("S-02", "Imports: numpy.linalg norm")])
    assert module_gates(_manifest({"dark/run/store.py": "S-01"}), g) == []


# -- M3 ----------------------------------------------------------------------


def test_a_module_implemented_from_two_slices_has_two_owners():
    g = Graph([_s("S-01"), _s("S-02"), _s("S-03")])
    m = _manifest({"dark/x.py": "S-01 S-02 S-03"}, a="S-01 S-02", b="S-03")
    found = module_gates(m, g)
    assert _ids(found, SPLIT_OWNERSHIP) == ["S-01", "S-02", "S-03"]
    assert "dark/x.py" in _detail(found, SPLIT_OWNERSHIP)


def test_a_module_shared_inside_one_slice_is_ordinary_overlap():
    g = Graph([_s("S-01"), _s("S-02")])
    m = _manifest({"dark/x.py": "S-01 S-02"}, a="S-01 S-02")
    assert module_gates(m, g) == []


def test_a_test_module_judging_two_slices_has_two_owners():
    """A test entry is in the slice of the contract it judges."""
    g = Graph([_s("S-01"), _s("S-02"),
               Entry(id=parse("T-01"), derives_from=(parse("S-01"),), title="t"),
               Entry(id=parse("T-02"), derives_from=(parse("S-02"),), title="t")])
    m = _manifest({"tests/test_x.py": "T-01 T-02", "dark/a.py": "S-01"}, a="S-01", b="S-02")
    assert _ids(module_gates(m, g), SPLIT_OWNERSHIP) == ["T-01", "T-02"]


def test_a_path_under_a_code_root_that_no_module_declares_is_refused():
    g = Graph([_s("S-01", "writes `dark/config/__init__.py` and `dark/run/`"),
               _s("S-02")])
    m = _manifest({"dark/run/store.py": "S-02"})
    found = module_gates(m, g)
    assert _ids(found, UNDECLARED_PATH) == ["S-01"]
    detail = _detail(found, UNDECLARED_PATH)
    assert "dark/config/__init__.py" in detail and "dark/run/" not in detail


def test_a_directory_holding_test_modules_is_not_a_code_root():
    """A test file is declared against test entries, written after the spec
    that names it; no spec entry could declare it."""
    g = Graph([_s("S-01", "scenarios in `tests/test_y.py`"), _s("S-02"),
               Entry(id=parse("T-01"), derives_from=(parse("S-02"),), title="t")])
    m = _manifest({"dark/run/store.py": "S-02", "tests/test_x.py": "T-01"})
    assert module_gates(m, g) == []


def test_a_path_named_only_to_say_it_must_not_exist_claims_nothing():
    g = Graph([_s("S-01", "so there is no `dark/report/runtime.py`"), _s("S-02")])
    assert module_gates(_manifest({"dark/run/store.py": "S-02"}), g) == []


def test_paths_outside_the_code_roots_are_out_of_scope():
    g = Graph([_s("S-01", "see `docs/a.md` and `pyproject.toml`"), _s("S-02")])
    assert module_gates(_manifest({"dark/run/store.py": "S-02"}), g) == []


# -- two successors ----------------------------------------------------------


def test_two_entries_superseding_one_id_are_refused():
    g = Graph([_s("S-01"), _s("S-02", supersedes="S-01"), _s("S-03", supersedes="S-01")])
    found = two_successors(g)
    assert [(f.kind, str(f.id)) for f in found] == [(TWO_SUCCESSORS, "S-01")]
    assert "S-02" in found[0].detail and "S-03" in found[0].detail
    assert any(f.kind == TWO_SUCCESSORS for f in admission_gates(g))


def test_a_retired_successor_still_counts():
    g = Graph([_s("S-01"), _s("S-02", supersedes="S-01"),
               _s("S-04", supersedes="S-02"), _s("S-03", supersedes="S-01")])
    assert _ids(two_successors(g), TWO_SUCCESSORS) == ["S-01"]


# -- wiring ------------------------------------------------------------------


def _amend(store, prompts, mid, entry):
    return call(store, prompts, "POST", "/projects/m/amendments",
                {"slice": "listings", "in_response_to": mid, "entries": [entry]})


def test_an_amendment_importing_without_the_edge_is_refused(store, prompts):
    mid = seed(store, prompts)
    status, payload = _amend(store, prompts, mid, {
        "layer": "S", "title": "b", "derives_from": ["A-01"],
        "body": "Imports: S-01 Index"})
    assert status == 409 and payload["admitted"] is False
    assert payload["findings"][0]["kind"] == BAD_IMPORT

    status, payload = _amend(store, prompts, mid, {
        "layer": "S", "title": "b", "derives_from": ["A-01"],
        "depends_on": ["S-01"], "body": "Imports: S-01 Index"})
    assert status == 200 and payload["admitted"] is True


def test_a_second_successor_is_refused(store, prompts):
    mid = seed(store, prompts)
    first = {"layer": "S", "title": "s2", "derives_from": ["A-01"], "supersedes": "S-01"}
    assert _amend(store, prompts, mid, first)[1]["admitted"] is True
    status, payload = _amend(store, prompts, mid, {**first, "title": "s3"})
    assert status == 409
    assert [f["kind"] for f in payload["findings"]] == [TWO_SUCCESSORS]


def test_a_project_with_no_imports_lines_and_no_modules_is_unaffected(store, prompts):
    mid = seed(store, prompts)
    status, payload = _amend(store, prompts, mid, {
        "layer": "S", "title": "b", "derives_from": ["A-01"],
        "body": "writes `dark/x.py`; imports `Index` (S-01)"})
    assert status == 200 and payload["admitted"] is True
    kinds = {f["kind"] for f in payload["gates"]["findings"]}
    assert not kinds & {BAD_IMPORT, NO_IMPORTS_LINE, BAD_MODULE_IMPORT,
                        SPLIT_OWNERSHIP, UNDECLARED_PATH, TWO_SUCCESSORS}
    assert payload["gates"]["sound"] is True


def test_split_ownership_makes_the_graph_unsound(store, prompts):
    mid = seed(store, prompts)
    _amend(store, prompts, mid, {"layer": "S", "title": "b", "derives_from": ["A-01"]})
    manifest = store.load_manifest("m")
    manifest.slices["other"] = Slice(name="other", members={parse("S-02")})
    manifest.slices["listings"].members.discard(parse("S-02"))
    store.save_manifest("m", manifest)
    call(store, prompts, "POST", "/projects/m/modules",
         {"path": "dark/x.py", "implements": ["S-01", "S-02"]})
    report = service._gate_report(store.load_graph("m"), store.load_manifest("m"))
    assert report["sound"] is False
    assert {f["kind"] for f in report["findings"]} >= {SPLIT_OWNERSHIP}
