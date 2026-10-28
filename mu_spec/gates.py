"""Admission gates: the mechanical checks a graph must pass.

From the design doc: "mechanical, run by the agent, human sees only
failures." A mechanical check does not need an agent -- it needs a function.
Keeping them here, in the unit, is what stops a session in a hurry from
skipping them, which is the entire point of a gate.

Three live here, because all three are answerable from the graph alone:

- orphans -- does every entry below trace to something above? This is a
  SOUNDNESS question: an orphan means the graph contains a claim that
  derives from nothing, which is never legitimate at any moment.
- bad dependencies -- does every same-layer edge point at a live entry in
  the same layer? Also a SOUNDNESS question, and the one that catches an
  entry still holding a dependency on a meaning that has since moved.
- unserved -- does every entry above have at least one entry below serving
  it? This is a COMPLETENESS question: it measures how far knowledge has
  actually been carried down, and being incomplete is the ordinary state of
  a project mid-propagation.
- untested -- does every spec entry have at least one test entry deriving
  from it? Also COMPLETENESS, and deliberately a separate finding rather
  than folding tests into `unserved`. Tests fork off spec instead of
  continuing the chain below it, so an untested spec entry is not a chain
  that stopped short -- it is a contract nobody has said how to falsify.
  Different question, different remedy, and only one of them means the
  pipeline has not finished propagating.

- bad imports -- does every entry a spec's `Imports:` line names have a
  depends_on edge from it? SOUNDNESS: an import with no edge is a
  dependency the scheduler cannot see. (A missing line is reported only.)
- two successors -- is every id superseded at most once? Refused at the
  amendment that would add the second claim; an existing fork is reported.

Keeping those apart matters, because they have different consequences.
Unsound blocks: an amendment that would introduce an orphan is refused, and
no work package is issued from a graph containing one. Incomplete does not
block -- it is the report of what is left to do.

Cycles between slices need the manifest -- which slice owns which
identifiers. They land with the manifest, not here: this module deliberately
knows nothing about slices.

Judgement gates are not here and never will be: an agent flagging what it
could not derive is not a computation.
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Callable

from mu_spec import body
from mu_spec.body import Import, parse as parse_body, strip_provenance
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import (
    ARCHITECTURE,
    BEHAVIOUR,
    LAYERS,
    SPEC,
    TEST,
    Identifier,
    InvalidIdentifier,
    derives_legally,
    parse,
    sort_key,
)

ORPHAN = "orphan"
UNSERVED = "unserved"
BAD_DEPENDENCY = "bad_dependency"
UNTESTED = "untested"
# An entry whose every derives-from problem is that a parent RETIRED. It
# still blocks -- work packages must be withheld until it is re-derived --
# but it is not the same event as a broken reference, and reporting it as
# one hides the difference between the ordinary course of a correction and
# a graph that never made sense. Its detail names the successor, so the
# remedy is in the finding.
STRANDED = "stranded"
# M2: a spec body imports from an entry it has no depends_on edge to.
BAD_IMPORT = "bad_import"
# Reported, never refused: a spec with no `Imports:` line, in a project that
# writes the line elsewhere.
NO_IMPORTS_LINE = "no_imports_line"
# Two entries supersede the same id. Refused at write time, reported after.
TWO_SUCCESSORS = "two_successors"
# A spec id in an entry's body that no edge of the entry reaches. Reported.
UNLINKED_MENTION = "unlinked_mention"
# A live entry whose body cites a retired id. Reported; tickets rewrite it.
RETIRED_MENTION = "retired_mention"
# The report-only kinds read out of code (D3: none of them ever refuses).
# M4: a name a spec uses or imports that no live spec defines.
UNDEFINED_NAME = "undefined_name"
# M6: a spec reads a member a producer's class does not declare.
UNKNOWN_MEMBER = "unknown_member"
# M9 (every B/A observable produced by an S) was built and dropped 10-24: on
# DARK it caught 0 of 2 cases and never fired -- B and A bodies there carry no
# code names, and the real faults were semantic. That belongs to the audits.
# M22 (a T building a type declared outside its anchor and `Exercises:`) was
# built and dropped 10-24 (6d36784): 0 of 3 cases, 37 T flagged at cut 45, 1 in
# 5 sampled real. DARK's broken fixtures lived in test helper code; no T body
# named the cell it built, while "a metric" and "does not construct a plant" did.


@dataclasses.dataclass(frozen=True)
class Finding:
    kind: str
    id: Identifier
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.id} -- {self.detail}"


# The layer boundaries are fixed, not inferred from what happens to be in
# the graph. An earlier version computed them from the entries present, which
# meant a project that had only reached behaviour treated behaviour as the
# bottom and cheerfully reported nothing missing -- it could not tell you
# knowledge had not yet reached spec, which is the single thing this gate is
# for. Intent can never be an orphan; spec can never be unserved, because
# what serves spec is code, tracked by module backlinks rather than entries.
_TOP_DEPTH = 0
_BOTTOM_DEPTH = len(LAYERS) - 1


def orphans(graph: Graph) -> list[Finding]:
    """An entry below the top layer must trace to something above it: at
    least one derives-from edge that resolves to a live entry exactly one
    layer up. One good parent is enough -- but a bad edge alongside it is
    still reported, because a dangling, sideways or layer-skipping reference
    is a defect someone has to fix and must not vanish behind a valid
    sibling."""
    findings: list[Finding] = []

    for entry in graph.entries():
        if entry.id.depth == _TOP_DEPTH:
            continue

        if not entry.derives_from:
            findings.append(
                Finding(ORPHAN, entry.id, "derives from nothing")
            )
            continue

        problems: list[str] = []
        # Kept apart from `problems` so that "every objection is a retired
        # parent" can be answered without parsing the message back out.
        retired: list[str] = []
        good = 0
        # A test derives from EXACTLY one spec entry, where every other layer
        # may derive from several. One scenario, one contract: a test citing
        # two contracts cannot say which one it falsifies when it fails, and
        # the ordering rule -- the unit implementing S*X follows the unit
        # holding tests derived from S*X -- would drag an unrelated
        # implementation unit behind it for a scenario that never judged it.
        if entry.id.layer == TEST and len(entry.derives_from) != 1:
            problems.append(
                f"a test derives from exactly one spec entry; {entry.id} "
                f"names {len(entry.derives_from)}"
            )

        for parent in entry.derives_from:
            if not derives_legally(entry.id, parent):
                problems.append(
                    f"{entry.id} must derive from the layer directly above; "
                    f"{parent} is not there"
                )
            elif graph.superseded_by(parent) is not None:
                retired.append(
                    f"{parent} is superseded by {graph.superseded_by(parent)}"
                )
            elif parent not in graph:
                problems.append(f"{parent} does not exist")
            else:
                good += 1

        # Stranded only when retirement is the WHOLE story. One live parent
        # alongside a retired one is still stranded -- the justification that
        # moved has to be re-derived either way -- but a dangling or
        # sideways reference in the same entry is a defect of its own and
        # outranks it.
        if problems:
            findings.append(
                Finding(ORPHAN, entry.id, "; ".join(problems + retired))
            )
        elif retired:
            findings.append(Finding(STRANDED, entry.id, "; ".join(retired)))
        elif good == 0:
            findings.append(Finding(ORPHAN, entry.id, "derives from nothing live"))

    return findings


def untested(graph: Graph) -> list[Finding]:
    """A live spec entry must have at least one live test entry deriving from
    it. A contract with no scenario is a contract nothing can falsify.

    Reported, never blocking -- for the same reason `unserved` does not
    block, and one sharper. Tests are written before the code and after the
    spec, so *every* spec entry is untested for the window between those two
    events. A gate here would refuse the project during the ordinary course
    of writing it.
    """
    return [
        Finding(UNTESTED, entry.id, "no test entry derives from it")
        for entry in graph.entries()
        if entry.id.layer == SPEC
        and not any(c.layer == TEST for c in graph.children(entry.id))
    ]


def unserved(graph: Graph) -> list[Finding]:
    """An entry above the bottom layer must have at least one live entry
    deriving from it. A requirement nothing serves is a requirement nobody
    built.

    Only a legal child counts. An entry two layers down claiming to serve
    this one is an illegal edge, and if it silenced this gate you could
    satisfy any requirement by jumping the layers that explain how -- the
    orphan gate would object, but the requirement would read as met.
    """
    return [
        Finding(UNSERVED, entry.id, "nothing derives from it")
        for entry in graph.entries()
        # A test is a leaf by construction -- nothing derives from one, ever
        # -- so asking this of it would report every test in the project
        # forever. What a test is missing is a module, and that is the module
        # map's report, not this one.
        if entry.id.layer != TEST
        and entry.id.depth != _BOTTOM_DEPTH
        and not any(
            derives_legally(child, entry.id) for child in graph.children(entry.id)
        )
    ]


def bad_dependencies(graph: Graph) -> list[Finding]:
    """A same-layer edge must point at a live entry in the same layer, and
    not at itself.

    A SOUNDNESS question, like orphans. Each of these means an entry is
    holding a dependency on something that is not there, is not what it
    thinks, or is not the kind of thing a horizontal edge may name -- and an
    entry that depends on a *retired* entry is precisely the case this edge
    exists to make visible: it consumed a meaning that has since moved.

    Cross-layer is refused rather than tolerated because it would be a
    derivation wearing the wrong label, and the orphan gate -- which only
    reads `derives_from` -- would never see it.
    """
    findings: list[Finding] = []
    for entry in graph.entries():
        problems: list[str] = []
        # A test carries no horizontal edge. One scenario
        # needs nothing from another scenario, and this is not tidiness: it
        # is what makes a work unit holding test modules a *root*. With no
        # outbound edge to have, a test unit cannot take part in a cycle, so
        # the ordering rule that puts implementation after tests can never
        # deadlock against a dependency -- arranged by the edge rules, not by
        # a scheduler.
        if entry.id.layer == TEST:
            if entry.depends_on:
                problems.append(
                    "a test entry depends on nothing -- one scenario needs "
                    "nothing from another, and an outbound edge is what "
                    "would let a test unit sit inside a cycle"
                )
        for target in entry.depends_on:
            if target == entry.id:
                problems.append(f"{target} depends on itself")
            elif target.layer != entry.id.layer:
                problems.append(
                    f"{target} is not in the same layer -- depends_on is "
                    "horizontal, and a cross-layer edge belongs in "
                    "derives_from where the orphan gate can see it"
                )
            elif graph.superseded_by(target) is not None:
                problems.append(
                    f"{target} is superseded by {graph.superseded_by(target)}"
                )
            elif target not in graph:
                problems.append(f"{target} does not exist")
        if problems:
            findings.append(Finding(BAD_DEPENDENCY, entry.id, "; ".join(problems)))
    return findings


# Bodies are append-only, so a body's facts never change. Every gate report
# reads every spec body, and a replay reads them 500 times over.
spec_facts = functools.lru_cache(maxsize=8192)(lambda text: body.parse(text, SPEC))
test_facts = functools.lru_cache(maxsize=8192)(lambda text: body.parse(text, TEST))


def strict_imports(entry: Entry) -> tuple[Import, ...] | None:
    """The live reader: a spec body's D4 `Imports:` line, or None when it has
    none. Only this reader ever refuses; `body.legacy_imports` exists for
    replaying pre-D4 bodies (replay/harness.py) and is never wired here."""
    facts = spec_facts(entry.body if isinstance(entry.body, str) else "")
    return facts.imports if facts.imports_line_present else None


Reader = Callable[[Entry], "tuple[Import, ...] | None"]


def live_successor(graph: Graph, identifier: Identifier) -> Identifier:
    """Where a chain of supersessions ends."""
    seen = {identifier}
    while (nxt := graph.superseded_by(identifier)) is not None and nxt not in seen:
        seen.add(nxt)
        identifier = nxt
    return identifier


def _carried(graph: Graph, identifier: Identifier) -> Identifier:
    """The entry an edge into `identifier` reads as: across preserving
    supersessions only, exactly as `Graph` carries a written edge."""
    seen = {identifier}
    while (nxt := graph.superseded_by(identifier)) is not None and nxt not in seen:
        successor = graph.get(nxt)
        if successor is None or not successor.preserves_interface:
            break
        seen.add(nxt)
        identifier = nxt
    return identifier


def imports(graph: Graph, read: Reader = strict_imports) -> list[Finding]:
    """M2: every entry a spec body imports from by id is one it depends on.

    A body that imports names from S-41 needs S-41's code to exist first, and
    the only thing that orders the work is the edge. An import with no edge is
    a dependency the scheduler cannot see -- DARK merged S-04 while its file
    imported a module that was not on main yet, and every later unit failed
    at collection.

    Blocking (`bad_import`). Module-path imports need the module map and are
    `slice_gates.module_gates`. A spec with no Imports line is not refused:
    it is reported (`no_imports_line`), and only once the project has started
    writing the line at all -- a project written before the convention has
    nothing to report, rather than every entry it holds.
    """
    findings: list[Finding] = []
    specs = [e for e in graph.entries() if e.id.layer == SPEC]
    read_all = {e.id: read(e) for e in specs}
    in_use = any(v is not None for v in read_all.values())
    for entry in specs:
        items = read_all[entry.id]
        if items is None:
            if in_use:
                findings.append(Finding(
                    NO_IMPORTS_LINE, entry.id,
                    "no `Imports:` line -- write one as the first line, "
                    "`Imports:` alone when it imports nothing",
                ))
            continue
        problems: list[str] = []
        for item in items:
            try:
                target = parse(item.target)
            except InvalidIdentifier:
                continue  # a module path
            names = ", ".join(item.names)
            if target == entry.id:
                problems.append(f"imports {names} from itself")
            elif target.layer != SPEC:
                problems.append(
                    f"{target} is not a spec entry -- Imports names the spec "
                    "entries whose code this one imports"
                )
            elif graph.get(target) is None:
                problems.append(f"{target} does not exist")
            elif _carried(graph, target) not in graph:
                # Retired: the import resolves to the live successor. An edge
                # to the successor satisfies it, and an edge to the retired
                # id is `bad_dependencies`' finding, not a second one here.
                live = live_successor(graph, target)
                if not {target, live, _carried(graph, target)} & set(entry.depends_on):
                    problems.append(
                        f"{target} is superseded by {live} -- import {names} "
                        f"from {live}, and add {live} to depends_on"
                    )
            elif (carried := _carried(graph, target)) not in entry.depends_on:
                now = "" if carried == target else f" (carried to {carried})"
                problems.append(
                    f"imports {names} from {target}{now} but does not depend on "
                    f"it -- add {carried} to depends_on"
                )
        if problems:
            findings.append(Finding(BAD_IMPORT, entry.id, "; ".join(problems)))
    return findings


def two_successors(graph: Graph) -> list[Finding]:
    """An entry is retired once, by one entry. Two entries superseding the
    same id fork its history: the live graph keeps one of them as the
    successor and the other silently stops being one, so which meaning
    replaced the old one depends on file order. Append-only makes it
    unrepairable after the fact, so the amendment that would create the
    second claim is the one refused (`service.submit_amendment`, on its own
    check). A fork already in the history is reported and does not make the
    graph unsound (Andrey, 10-25): blocking on something no write can repair
    would freeze the project for good, as it froze DARK."""
    return [
        Finding(
            TWO_SUCCESSORS,
            old,
            f"{old} has two successors, {', '.join(map(str, new))} -- an entry "
            f"is superseded once. Supersede {live_successor(graph, new[0])} "
            "instead, so the history "
            "stays one chain",
        )
        for old, new in graph.contested().items()
    ]


def unlinked_mentions(graph: Graph) -> list[Finding]:
    """A spec id named in an entry's body that none of its edges reach --
    derives_from, depends_on, supersedes, or its own id. A
    reference the graph cannot see is one a supersession will not flag.

    COMPLETENESS, reported and never blocking (D3): prose may name an entry
    for a reason no edge expresses. Provenance lines are history, not
    references, so they are read without (body.strip_provenance)."""
    findings: list[Finding] = []
    for entry in graph.entries():
        linked = {str(entry.id), *map(str, entry.derives_from), *map(str, entry.depends_on)}
        if entry.supersedes is not None:
            linked.add(str(entry.supersedes))
        named = parse_body(strip_provenance(entry.body), entry.id.layer).id_mentions
        loose = [i for i in named if i.startswith(SPEC) and i not in linked]
        if loose:
            findings.append(Finding(UNLINKED_MENTION, entry.id,
                                    f"names {', '.join(loose)} in its body with no edge to "
                                    f"{'it' if len(loose) == 1 else 'them'}"))
    return findings


def retired_mentions(graph: Graph) -> list[Finding]:
    """A live entry whose body still cites a retired id. Tickets rewrite it
    to the live successor as they render; this says which stored prose to
    clean. COMPLETENESS, reported and never blocking (D3 ruling (b)).
    Provenance lines are history and are read without."""
    findings: list[Finding] = []
    for entry in graph.entries():
        named = parse_body(strip_provenance(entry.body), entry.id.layer).id_mentions
        stale = []
        for cited in named:
            old = parse(cited)
            end = live_successor(graph, old)
            if end != old:
                stale.append(f"{old} (now {end if end in graph else 'no live successor'})")
        if stale:
            findings.append(Finding(RETIRED_MENTION, entry.id,
                                    f"cites retired {', '.join(stale)} in its body"))
    return findings


# -- read out of code: reported, never refused (D3) ---------------------------
#
# Each of these is a regex over stubs and fragments, so each is a proxy, and a
# proxy never gates (CLAUDE.md). Every one leans towards silence: a name it
# cannot place is dropped rather than flagged. The DARK replay is where their
# noise is measured (replay/README.md).

code = functools.lru_cache(maxsize=8192)(body.code_facts)


def _stdlib_names() -> frozenset[str]:
    """Public names of the modules a spec's stubs lean on without saying so.
    Not this project's to define, so never an undefined name."""
    import importlib

    names: set[str] = set()
    for mod in (
        "builtins typing collections collections.abc dataclasses enum pathlib "
        "datetime decimal fractions numbers io types functools itertools "
        "contextlib abc uuid logging json re os math statistics threading "
        "asyncio subprocess hashlib tempfile argparse sqlite3 csv operator "
        "random string time warnings traceback inspect copy heapq bisect "
        "shutil socket struct queue weakref tomllib"
    ).split():
        try:
            names |= {n for n in dir(importlib.import_module(mod)) if not n.startswith("_")}
        except ImportError:
            continue  # tomllib before 3.11
    return frozenset(names)


STDLIB_NAMES = _stdlib_names()


def _specs(graph: Graph) -> list[Entry]:
    return [e for e in graph.entries() if e.id.layer == SPEC and isinstance(e.body, str)]


def _stems(entry: Entry) -> set[str]:
    """`pkg/model.py` -> `model`: a module imported by name is not a name."""
    return {
        p.rsplit("/", 1)[-1].split(".")[0]
        for p in spec_facts(entry.body).path_mentions
    }


def undefined_names(graph: Graph, read: Reader = strict_imports) -> list[Finding]:
    """M4: a name a spec imports from an entry (`Imports:`), or a CamelCase
    type its code uses, that no live spec defines -- in a code block, as a
    top-level def, class or assignment. DARK's S-196 typed `regenerate()`
    over `Analysis`, which nothing ever defined.

    "Defined" is anywhere in the live graph, not only by the entry imported
    from: a name in the wrong entry is M2's question. Standard-library names,
    the body's own bindings and parameters, and module stems are left out."""
    specs = _specs(graph)
    defined: set[str] = set()
    for entry in specs:
        defined |= code(entry.body).local | _stems(entry)
    findings: list[Finding] = []
    for entry in specs:
        facts = code(entry.body)
        items = read(entry) or ()
        imported: list[str] = []
        from_modules: set[str] = set()
        for item in items:
            try:
                parse(item.target)
            except InvalidIdentifier:
                from_modules.update(item.names)
            else:
                imported += item.names
        # Two letters (`Wm`, `Pa`) is a maths symbol in a stub, not a type.
        used = sorted(n for n in facts.references - from_modules if len(n) > 2)
        missing = [
            n for n in dict.fromkeys(imported + used)
            if n not in defined and n not in STDLIB_NAMES
        ]
        if missing:
            findings.append(Finding(
                UNDEFINED_NAME, entry.id,
                f"uses {', '.join(f'`{n}`' for n in missing)}, which no live spec "
                "entry defines in a code block -- define it in the entry that "
                "owns it (and depend on that entry), or correct the name",
            ))
    return findings


# A file name read as `x.attr` (`plant.py`) is not a member read.
_EXTENSIONS = frozenset("py json jsonl toml md npy npz txt yaml yml png csv cfg ini lock".split())
# Bases whose members are all declared in the class itself. Any other base
# may carry the member, so a class with one is never judged.
_PLAIN_BASES = frozenset({"Protocol", "NamedTuple", "StrEnum", "Enum", "IntEnum", "TypedDict", "object"})


def _camel(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.split("_") if p)


def unknown_members(graph: Graph) -> list[Finding]:
    """M6: a spec reads `x.attr` -- `cell.n_drones`, `Cell.n_drones` --
    where `Cell` is a class another live spec declares, and no declaration of
    it has that member. DARK's S-77 sized a channel by `cell.n_drones`; the
    `Cell` of S-38 had no such field.

    The variable is matched to the class by name only (`run_config` ->
    `RunConfig`), members are pooled over every live declaration of the
    class, and a class with a base outside `_PLAIN_BASES`, or with no members
    read, is never judged."""
    specs = _specs(graph)
    members: dict[str, set[str]] = {}
    bases: dict[str, set[str]] = {}
    owners: dict[str, list[str]] = {}
    for entry in specs:
        for name, base, ms in code(entry.body).classes:
            members.setdefault(name, set()).update(ms)
            bases.setdefault(name, set()).update(base)
            owners.setdefault(name, []).append(str(entry.id))
    findings: list[Finding] = []
    for entry in specs:
        facts = code(entry.body)
        own = {c for c, _, _ in facts.classes}
        bad: list[str] = []
        for var, attr in facts.attributes:
            cls = var if var[:1].isupper() else _camel(var)
            if (
                var == "self" or attr.startswith("__") or attr in _EXTENSIONS
                or cls in own or cls not in members or not members[cls]
                or bases[cls] - _PLAIN_BASES or attr in members[cls]
            ):
                continue
            bad.append(f"`{cls}.{attr}` ({', '.join(owners[cls])} declares {cls})")
        if bad:
            findings.append(Finding(
                UNKNOWN_MEMBER, entry.id,
                f"reads {'; '.join(dict.fromkeys(bad))}, but no declaration of the "
                "class has that member -- add it to the producer (an issue against "
                "it), or read what the producer does declare",
            ))
    return findings


def admission_gates(graph: Graph) -> list[Finding]:
    """Every mechanical finding, in spine order -- the human sees only
    failures, so this list is the whole report and should read top-down the
    way the graph does."""
    findings = (
        orphans(graph)
        + bad_dependencies(graph)
        + unserved(graph)
        + untested(graph)
        + imports(graph)
        + two_successors(graph)
        + unlinked_mentions(graph)
        + retired_mentions(graph)
        + undefined_names(graph)
        + unknown_members(graph)
    )
    return sorted(findings, key=lambda f: (sort_key(f.id), f.kind))
