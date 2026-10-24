"""The report-only kinds (D3): M4 undefined names, M5 same-wave edges, M6
unknown members, M7 sweep collisions, M10 specs with no module (M9 was
dropped). Every one REPORTS; none may ever refuse."""

from __future__ import annotations

from mu_spec import service
from mu_spec.body import legacy_imports
from mu_spec.gates import (
    UNDEFINED_NAME,
    UNKNOWN_MEMBER,
    undefined_names,
    unknown_members,
)
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.slice_gates import NO_MODULE, SWEEP_COLLISION, no_module, sweep_collisions
from mu_spec.storage import Manifest, Slice
from mu_spec.waves import SAME_WAVE_EDGE, same_wave_edges

from test_server import call, prompts, register_slice, seed, store  # noqa: F401


def _e(ident, body="", depends_on="", derives="A-01"):
    return Entry(
        id=parse(ident),
        derives_from=(parse(derives),) if derives else (),
        depends_on=tuple(parse(d) for d in depends_on.split()),
        title=ident,
        body=body,
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


def _code(text):
    return f"```python\n{text}\n```"


NEW_KINDS = (UNDEFINED_NAME, UNKNOWN_MEMBER, NO_MODULE, SWEEP_COLLISION, SAME_WAVE_EDGE)


def test_no_new_kind_ever_blocks():
    assert not set(NEW_KINDS) & set(service._BLOCKING)


# -- M4 ----------------------------------------------------------------------


def test_a_type_no_live_entry_defines_is_reported():
    g = Graph([
        _e("S-01", _code("class Statement:\n    verdict: str")),
        _e("S-02", _code("FIGURES: Mapping[str, Callable[[Analysis], Statement]]\n"
                         "def regenerate(analysis: Analysis, out: Path) -> None: ...")),
    ])
    found = undefined_names(g)
    assert _ids(found, UNDEFINED_NAME) == ["S-02"]
    detail = found[0].detail
    assert "`Analysis`" in detail and "Statement" not in detail
    # stdlib and typing names are not this project's to define
    assert "Mapping" not in detail and "Path" not in detail


def test_parameters_aliases_and_local_assignments_are_not_references():
    g = Graph([_e("S-02", _code("def execute(): ...")),
               _e("S-01", "Imports `execute` as `step_run` (S-02).\n\n"
                  + _code("Scalar = str | int\n"
                          "def fuse(xa, Pa, xb, Pb) -> Scalar: ...\n"
                          "def sigma(x) -> tuple[pts, Wm, Wc]: ...\n"
                          "step_run(1)"))])
    assert undefined_names(g, read=lambda e: legacy_imports(e.body) or None) == []


def test_an_imported_name_nobody_defines_is_reported():
    g = Graph([
        _e("S-01", _code("def record(x): ...")),
        _e("S-02", "Imports: S-01 record, record_event", "S-01"),
    ])
    found = undefined_names(g)
    assert _ids(found, UNDEFINED_NAME) == ["S-02"]
    assert "`record_event`" in found[0].detail and "`record`" not in found[0].detail


def test_an_imported_module_stem_is_not_an_undefined_name():
    g = Graph([
        _e("S-01", "Module: `pkg/model.py`.\n\n" + _code("def f(): ...")),
        _e("S-02", "Imports: S-01 model", "S-01"),
    ])
    assert undefined_names(g) == []


def test_names_in_strings_and_comments_are_not_references():
    g = Graph([_e("S-01", _code('x = "Analysis"  # Analysis lives elsewhere\n'
                                '"""Prediction is described here."""'))])
    assert undefined_names(g) == []


# -- M6 ----------------------------------------------------------------------


_CELL = _code("@dataclass(frozen=True)\nclass Cell:\n    rates: float\n    anchor: int\n\n"
              "    def dt(self) -> float: ...")


def test_reading_a_field_the_producer_does_not_declare_is_reported():
    g = Graph([
        _e("S-01", _CELL),
        _e("S-02", _code('ChannelSpec("command", "<f8", (cell.n_drones, 3))\n'
                         "x = cell.rates; y = cell.dt()"), "S-01"),
    ])
    found = unknown_members(g)
    assert _ids(found, UNKNOWN_MEMBER) == ["S-02"]
    assert "Cell.n_drones" in found[0].detail and "rates" not in found[0].detail
    assert "S-01" in found[0].detail


def test_a_class_with_an_unknown_base_is_never_judged():
    g = Graph([
        _e("S-01", _code("class Cell(Base):\n    rates: float")),
        _e("S-02", "`cell.n_drones`"),
    ])
    assert unknown_members(g) == []


def test_file_names_and_dunders_are_not_member_reads():
    g = Graph([
        _e("S-01", _code("class Plant:\n    t: float")),
        _e("S-02", "writes `plant.py`; `Plant.__post_init__` raises"),
    ])
    assert unknown_members(g) == []


# -- M10 ---------------------------------------------------------------------


def test_a_spec_no_module_implements_is_reported_once_modules_are_in_use():
    g = Graph([_e("S-01"), _e("S-02")])
    assert _ids(no_module(_manifest(listings="S-01 S-02"), g), NO_MODULE) == []
    m = _manifest({"pkg/a.py": "S-01"}, listings="S-01 S-02")
    found = no_module(m, g)
    assert _ids(found, NO_MODULE) == ["S-02"]
    assert "declare_module" in [f for f in found if f.kind == NO_MODULE][0].detail


# -- M7 ----------------------------------------------------------------------


_SWEEP = ("**Nothing outside this module may name a variant.** `tests/test_swarm.py` "
          "sweeps `pkg/` for the string literals in `VARIANT_NAMES`, allowlisting "
          "`pkg/control/variant.py`.\n\n" + _code("VARIANT_NAMES = ('direct', 'ekf')"))


def test_a_sibling_required_to_use_what_a_sweep_bans_is_reported_on_both():
    g = Graph([
        _e("S-01", _SWEEP),
        _e("S-02", _code("variant: str = setting(check=lambda v: v in VARIANT_NAMES)")),
        _e("S-03", _code("VARIANT = VARIANT_NAMES[0]")),
    ])
    m = _manifest({"pkg/control/variant.py": "S-01", "pkg/config/schema.py": "S-02",
                   "other/x.py": "S-03"}, listings="S-01 S-02 S-03")
    found = sweep_collisions(m, g)
    assert _ids(found, SWEEP_COLLISION) == ["S-01", "S-02"]
    detail = " | ".join(f.detail for f in found if f.kind == SWEEP_COLLISION)
    assert "VARIANT_NAMES" in detail and "pkg/config/schema.py" in detail


def test_an_allowlisted_file_or_a_prose_mention_is_no_collision():
    g = Graph([
        _e("S-01", _SWEEP),
        _e("S-02", "Imports `VARIANT_NAMES` (S-01) and holds no literal."),
        _e("S-03", _code("x = VARIANT_NAMES")),
    ])
    m = _manifest({"pkg/control/variant.py": "S-01 S-03", "pkg/config/schema.py": "S-02"},
                  listings="S-01 S-02 S-03")
    assert _ids(sweep_collisions(m, g), SWEEP_COLLISION) == []


# -- M5 ----------------------------------------------------------------------


def test_an_edge_between_two_slices_of_one_wave_is_reported():
    before = Graph([_e("S-01"), _e("S-02")])
    before_m = _manifest(a="S-01", b="S-02")
    after = Graph([_e("S-01"), _e("S-02"), _e("S-03", depends_on="S-02")])
    after_m = _manifest(a="S-01 S-03", b="S-02")
    found = same_wave_edges(before, before_m, after, after_m)
    assert _ids(found, SAME_WAVE_EDGE) == ["S-03"]
    assert "wave 0" in found[0].detail and "S-02" in found[0].detail


def test_an_edge_into_an_earlier_wave_is_the_ordinary_course():
    before = Graph([_e("S-01", depends_on="S-02"), _e("S-02")])
    before_m = _manifest(a="S-01", b="S-02")
    after = Graph([*before.entries(), _e("S-03", depends_on="S-02")])
    after_m = _manifest(a="S-01 S-03", b="S-02")
    assert same_wave_edges(before, before_m, after, after_m) == []


def test_a_slice_with_no_entries_yet_sits_in_wave_zero():
    before = Graph([_e("S-02")])
    before_m = _manifest(b="S-02")
    after = Graph([_e("S-02"), _e("S-03", depends_on="S-02")])
    after_m = _manifest(a="S-03", b="S-02")
    assert _ids(same_wave_edges(before, before_m, after, after_m), SAME_WAVE_EDGE) == ["S-03"]


def test_the_amendment_reports_a_same_wave_edge_and_admits_it(store, prompts):
    mid = seed(store, prompts)
    register_slice(store, "other")
    call(store, prompts, "POST", "/projects/m/amendments", {
        "slice": "other", "in_response_to": mid,
        "entries": [{"layer": "S", "title": "o", "derives_from": ["A-01"]}]})
    status, payload = call(store, prompts, "POST", "/projects/m/amendments", {
        "slice": "listings", "in_response_to": mid,
        "entries": [{"layer": "S", "title": "n", "derives_from": ["A-01"],
                     "depends_on": ["S-02"]}]})
    assert status == 200 and payload["admitted"] is True
    assert [f["id"] for f in payload["same_wave_edges"]] == ["S-03"]
    assert payload["gates"]["sound"] is True


# -- M22 ---------------------------------------------------------------------


def _t(ident, body, anchor="S-01"):
    return _e(ident, body, derives=anchor)


_SPECS = [
    _e("S-01", _code("def resolve(doc): ...")),
    _e("S-02", _code("@dataclass(frozen=True)\nclass Cell:\n    rates: float")),
    _e("S-03", _code("class RunConfig:\n    seed: int")),
]


def test_a_scenario_constructing_a_type_its_anchor_does_not_define_is_reported():
    from mu_spec.gates import UNNAMED_FIXTURE, unnamed_fixtures
    g = Graph([*_SPECS, _t("T-01", "Build `Cell(rates=1.0)` and resolve it.")])
    found = unnamed_fixtures(g)
    assert _ids(found, UNNAMED_FIXTURE) == ["T-01"]
    assert "`Cell`" in found[0].detail and "S-02" in found[0].detail
    assert "Exercises:" in found[0].detail


def test_a_fixture_described_in_prose_is_reported():
    from mu_spec.gates import UNNAMED_FIXTURE, unnamed_fixtures
    g = Graph([*_SPECS, _t("T-01", "Build a run config from the smallest document. "
                                   "Resolving it is refused.")])
    found = unnamed_fixtures(g)
    assert _ids(found, UNNAMED_FIXTURE) == ["T-01"]
    assert "`RunConfig`" in found[0].detail and "S-03" in found[0].detail


def test_exercises_the_anchor_or_no_live_definition_silences_it():
    from mu_spec.gates import unnamed_fixtures
    g = Graph([
        *_SPECS,
        _t("T-01", "Exercises: S-02\n\nBuild `Cell(rates=1.0)`."),
        _t("T-02", "Build `Cell(rates=1.0)`.", anchor="S-02"),
        _t("T-03", "Build `Plant(1)`. A run config is only mentioned."),
        _t("T-04", "A cell is refused."),
    ])
    assert unnamed_fixtures(g) == []


def test_unnamed_fixture_is_in_the_report_and_never_blocks():
    from mu_spec.gates import UNNAMED_FIXTURE, admission_gates
    assert UNNAMED_FIXTURE not in service._BLOCKING
    g = Graph([*_SPECS, _t("T-01", "Build `Cell(rates=1.0)`.")])
    assert UNNAMED_FIXTURE in {f.kind for f in admission_gates(g)}
