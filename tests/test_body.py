"""Body parsing: structured facts read out of an entry's prose.

The fixtures marked DARK are real bodies from the DARK snapshot, trimmed to
the parts under test and copied here as strings so the suite never reads the
snapshot. Ids are written with identifiers' separators, never by hand.
"""

from __future__ import annotations

from mu_spec.body import BodyFacts, Import, legacy_imports, parse
from mu_spec.identifiers import LEGACY_SEPARATOR as DOT, SEPARATOR as SEP, SPEC, TEST, INTENT


def sid(n: int, sep: str = SEP) -> str:
    return f"{SPEC}{sep}{n}"


def tid(n: int, sep: str = SEP) -> str:
    return f"{TEST}{sep}{n}"


# -- strict Imports line ----------------------------------------------------


def test_imports_line_with_ids_and_module_paths():
    body = f"Imports: {sid(41)} Subspace, as_fact; dark.record RunRegion; {sid(9, DOT)} Arrangement\n\nProse."
    facts = parse(body, SPEC)
    assert facts.imports_line_present
    assert facts.imports == (
        Import("S-41", ("Subspace", "as_fact")),
        Import("dark.record", ("RunRegion",)),
        Import("S-09", ("Arrangement",)),
    )
    assert facts.problems == ()
    assert "dark.record" in facts.path_mentions


def test_imports_must_be_the_first_non_empty_line():
    facts = parse(f"\n\n  \nImports: {sid(3)} RunConfig\n", SPEC)
    assert facts.imports == (Import("S-03", ("RunConfig",)),) and facts.problems == ()

    late = parse(f"Module: x.\nImports: {sid(3)} RunConfig\n", SPEC)
    assert late.imports_line_present
    assert late.imports == (Import("S-03", ("RunConfig",)),)
    assert any("first" in p for p in late.problems)


def test_malformed_items_become_problems_and_are_skipped():
    body = f"Imports: {sid(41)}; {sid(3)} Good; Bad Name; dark.mod not-a-name; ;{sid(7)} A,,B"
    facts = parse(body, SPEC)
    assert facts.imports == (Import("S-03", ("Good",)),)
    assert len(facts.problems) == 5


def test_an_empty_imports_line_imports_nothing():
    facts = parse("Imports:\n\nbody", SPEC)
    assert facts.imports_line_present and facts.imports == () and facts.problems == ()


def test_an_s_without_an_imports_line_does_not_crash():
    facts = parse("Just prose.", SPEC)
    assert facts.imports_line_present is False
    assert facts.imports == () and facts.problems == ()


def test_errors_line():
    facts = parse(f"Imports: {sid(1)} A\nErrors: ConfigError, Inadmissible\n", SPEC)
    assert facts.errors == ("ConfigError", "Inadmissible")
    assert parse("Errors: ok, 9bad", SPEC).problems != ()


def test_layer_lines_only_read_on_their_layer():
    body = f"Imports: {sid(1)} A\nExercises: {sid(2)}"
    assert parse(body, INTENT).imports == () and parse(body, INTENT).exercises == ()
    assert parse(body, SPEC).exercises == ()
    assert parse(body, TEST).imports == ()


# -- T bodies ---------------------------------------------------------------


def test_t_with_exercises():
    facts = parse(f"Run it.\nExercises: {sid(250)}, {sid(266, DOT)}\n", TEST)
    assert facts.exercises == ("S-250", "S-266")
    assert facts.problems == ()


def test_t_exercises_malformed_item():
    facts = parse(f"Exercises: {sid(250)}, nonsense", TEST)
    assert facts.exercises == ("S-250",) and len(facts.problems) == 1


def test_t_without_exercises_dark():
    # DARK T-128, verbatim.
    body = (
        "Run the health command over a path whose stages all pass except some that report "
        "missing preconditions. The process exits zero. The output names each missing "
        "precondition together with its detail, and carries a summary line stating how many are missing."
    )
    facts = parse(body, TEST)
    assert facts.exercises == () and facts.problems == () and facts.id_mentions == ()


# -- defines ----------------------------------------------------------------


def test_defines_top_level_only():
    body = (
        "```python\n"
        "LIMIT = 3\n"
        "Pair: TypeAlias = tuple[int, int]\n"
        "TYPED: int = 4\n"
        "@dataclass\n"
        "class Box:\n"
        "    INNER = 1\n"
        "    def method(self): ...\n"
        "    class Nested: ...\n"
        "def outer():\n"
        "    def inner(): ...\n"
        "async def later(): ...\n"
        "lowercase = 2\n"
        "```\n"
        "```toml\nKEY = 1\n```\n"
        "def not_in_a_block(): ...\n"
    )
    assert parse(body, SPEC).defines == ("LIMIT", "TYPED", "Box", "outer", "later")


def test_an_unclosed_fence_is_a_problem_and_still_read():
    facts = parse("```python\ndef f(): ...\n", SPEC)
    assert facts.defines == ("f",)
    assert len(facts.problems) == 1


# -- mentions ---------------------------------------------------------------


def test_both_id_spellings_are_mentions_normalised_and_deduped():
    body = f"See {sid(12, DOT)} and {sid(12)}, {tid(5)}, and A{DOT}44. Not S{DOT}cell, not XS{SEP}1."
    assert parse(body, SPEC).id_mentions == ("S-12", "T-05", "A-44")


def test_path_mentions():
    body = "Edits `dark/config/__init__.py` and dark/health/. Not and/or, not S/T."
    assert parse(body, SPEC).path_mentions == ("dark/config/__init__.py", "dark/health/")


def test_a_heading_inside_a_body_truncates_nothing():
    # DARK S-104, trimmed: the mentions and the code after `##` must survive (e2764c1).
    body = (
        f"Module: `dark/config/resolve.py`, continuing {sid(4, DOT)}.\n\n"
        "## The rule\n\n"
        f"**`resolve` applies the refusals.** {sid(69, DOT)} makes the sentinel the rule.\n\n"
        "## Why\n\n```python\ndef resolve(doc): ...\n```\n"
    )
    facts = parse(body, SPEC)
    assert facts.id_mentions == ("S-04", "S-69")
    assert facts.defines == ("resolve",)
    assert facts.path_mentions == ("dark/config/resolve.py",)


def test_empty_body():
    assert parse("", SPEC) == BodyFacts()
    assert parse("", TEST) == BodyFacts()
    assert legacy_imports("") == ()


# -- real DARK bodies -------------------------------------------------------

S_41 = (
    "Module: `dark/estimation/anchor.py`. NumPy — `numpy.linalg.svd`. Imports `Arrangement` (S·09) "
    "and `Layout` (S·37).\n\n"
    "```python\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class Subspace:\n"
    '    """The directions of the joint configuration the available information determines."""\n'
    "    basis: npt.NDArray[np.float64]      # (n, k) orthonormal columns spanning the observable part\n"
    "    dim: int                            # k\n"
    "    full: int                           # n\n\n"
    "    def project(self, v: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]: ...\n"
    "    def restrict(self, P: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]: ...  # B.T P B\n\n"
    "def observable_subspace(sensing: Arrangement, anchor: int, layout: Layout,\n"
    "                        reference: npt.NDArray[np.float64]) -> Subspace: ...\n\n"
    "def as_fact(s: Subspace) -> Fact: ...     # array-valued, for S·27\n"
    "```\n\n"
    "**Computed once, before the first step, from run-level facts only.** It is therefore a fact implied "
    "by the resolved inputs, which is exactly the class S·27 seals, and the basis lands in "
    "`facts/observable_basis.npy` beside the JSON.\n"
)

S_275 = (
    "Module: `dark/runtime/loop.py`. NumPy. Imports `Plant`/`Instant` (S·87); `check` (S-228); "
    "`build_cell` and `Cell` (S·38); `VARIANTS` and `VariantContract` (S·34); `build_views` (S·35); "
    "`clean_ranges` (S·13); `build`/`apply`/`unconsumed` (S·18); `Inbox` admission and `Publication` "
    "(S·45, S·40); `observable_subspace` and `as_fact` (S·41); and from `dark.record`: `RunRegion`, "
    "`Recorder`, `ChannelSpec`, `Fact`, `executing`. It is also ordered after the variant implementations "
    "`VARIANTS` dispatches to -- `direct` (S·111) and the filtered `ekf`/`ukf`/`naive` (S·49) -- because "
    "a step calls them.\n\n"
    "```python\n"
    'TRUTH_STATE = ChannelSpec("truth.state", "<f8", (N, 6))   # per drone: position, velocity\n'
    "MEASUREMENT = (\n"
    '    ChannelSpec("measurement.range",       "<f8", (E,)),\n'
    ")\n"
    'LOOP_REQUIRED = frozenset({"truth.state", "step.t"})\n\n'
    "@dataclass(frozen=True, slots=True)\n"
    "class Prepared:\n"
    "    cfg: RunConfig\n\n"
    "def prepare(cfg: RunConfig) -> Prepared:\n"
    '    """Construct the run from its resolved configuration."""\n\n'
    "def execute(region: RunRegion, prepared: Prepared, plant: Plant) -> None:\n"
    '    """Step the prepared run to its end."""\n'
    "```\n"
)

S_131 = (
    "Module: `dark/record/determining.py`. Stdlib only — `dataclasses`, `json`, `hashlib`, `re`. "
    "Imports `RunConfig` from `dark.config.schema` as a type and writes nothing into it.\n\n"
    "```python\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class Determining:\n"
    "    config: RunConfig\n\n"
    "    @property\n"
    "    def seed(self) -> int:\n"
    "        return self.config.experiment.seed\n\n"
    "AMBIENT = re.compile(...)   # what it recognises is stated below\n"
    "```\n"
)

S_304 = (
    "Module: `dark/report/figures.py`. `matplotlib` with the `Agg` backend, NumPy. Imports "
    "`Analysis`/`Statement`/`Verdict` (S-195), `RUN_METRICS`/`FAMILY_METRICS` (S-296) and `Cited`/`key` "
    "(S-58). About two hundred lines, thirty of them shared axis furniture.\n\n"
    "```python\n"
    "FIGURES: Mapping[str, Callable[[Analysis], Figure]]    # five entries\n\n"
    "def regenerate(analysis: Analysis, out_dir: Path) -> tuple[Figure, ...]: ...\n"
    "```\n"
)

S_03 = (
    "Module: `dark/config/schema.py`. Depends only on `declare.py` and `dataclasses`.\n\n"
    "```python\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class RunConfig:\n"
    '    """Every setting the system recognises, each with the value it took."""\n'
    "```\n\n"
    "**Every field has a default.** That is what makes A·01's claim true by construction.\n"
)


def test_dark_s41():
    facts = parse(S_41, SPEC)
    assert facts.imports_line_present is False
    assert facts.defines == ("Subspace", "observable_subspace", "as_fact")
    assert facts.id_mentions == ("S-09", "S-37", "S-27")
    assert facts.path_mentions == ("dark/estimation/anchor.py", "facts/observable_basis.npy")
    assert legacy_imports(S_41) == (Import("S-09", ("Arrangement",)), Import("S-37", ("Layout",)))


def test_dark_s275_legacy_imports():
    imports = legacy_imports(S_275)
    assert imports == (
        Import("S-87", ("Plant", "Instant")),
        Import("S-228", ("check",)),
        Import("S-38", ("build_cell", "Cell")),
        Import("S-34", ("VARIANTS", "VariantContract")),
        Import("S-35", ("build_views",)),
        Import("S-13", ("clean_ranges",)),
        Import("S-18", ("build", "apply", "unconsumed")),
        Import("S-45", ("Inbox", "Publication")),
        Import("S-40", ("Inbox", "Publication")),
        Import("S-41", ("observable_subspace", "as_fact")),
        Import("dark.record", ("RunRegion", "Recorder", "ChannelSpec", "Fact", "executing")),
    )
    assert parse(S_275, SPEC).defines == (
        "TRUTH_STATE", "MEASUREMENT", "LOOP_REQUIRED", "Prepared", "prepare", "execute",
    )


def test_dark_s131_legacy_import_from_a_module():
    assert legacy_imports(S_131) == (Import("dark.config.schema", ("RunConfig",)),)
    assert parse(S_131, SPEC).defines == ("Determining", "AMBIENT")


def test_dark_s304_new_spelling():
    assert legacy_imports(S_304) == (
        Import("S-195", ("Analysis", "Statement", "Verdict")),
        Import("S-296", ("RUN_METRICS", "FAMILY_METRICS")),
        Import("S-58", ("Cited", "key")),
    )
    assert parse(S_304, SPEC).defines == ("FIGURES", "regenerate")


def test_dark_s03_has_no_imports():
    facts = parse(S_03, SPEC)
    assert facts.imports_line_present is False and facts.problems == ()
    assert legacy_imports(S_03) == ()
    assert facts.defines == ("RunConfig",)
    assert facts.id_mentions == ("A-01",)
    assert facts.path_mentions == ("dark/config/schema.py",)
