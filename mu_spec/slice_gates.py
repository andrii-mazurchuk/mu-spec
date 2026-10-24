"""The mechanical checks that need the manifest.

`gates.py` deliberately knows nothing about slices -- it answers questions
about entries and their edges alone. Everything here needs to know which
slice owns which identifier, and which slices are cross-cutting, so it lives
here instead. Two of the checks are about the *projected* slice graph and
name a slice; one is about an entry's edges and names an entry.

All are **blocking**, and that is a decision rather than a default. None of
them is a condition you propagate through and tidy up afterwards -- they mean
the structure is wrong, and every entry derived while one stands is derived
against a structure that does not hold. Blocking early is cheap: reslicing
before anything derives from a slice costs nothing.

- **A cycle in the slice dependency graph.** A slice is the unit of work, so
  two slices that need each other cannot be ordered and neither can start.
  This is not a scheduling failure to be worked around; it is the slicing
  being wrong. Note the entry graph itself can never cycle -- `derives_from`
  runs strictly one layer up and `depends_on` strictly within a layer -- so a
  cycle here is always about how the entries were *grouped*, never about the
  entries.

- **A cross-cutting slice depending on a feature slice.** If it has to ask a
  feature slice for something, it needs to know its caller, which is exactly
  what its classification says it does not. Depending on another
  cross-cutting slice is fine; the cycle check still applies there.

- **An entry in more than one slice.** Slices define write ownership, and
  overlapping ownership is no ownership: two work packages would hand the
  same entry out as editable, and the audit would pass for both.

- **An edge of the wrong kind for what it points at.** An edge into a
  cross-cutting slice is an emission, and an emission goes nowhere else.
  This is what makes the classification enforceable rather than declarative:
  once a slice is ruled cross-cutting, the only legal way to reach it stops
  imposing order on it.

There is no judgement here, and never will be. Whether a slice *is*
cross-cutting is a call an agent argues and a human rules on. Once that
ruling is recorded, whether the edges are legal is arithmetic.
"""

from __future__ import annotations

import dataclasses
import re

from mu_spec.gates import Finding, Reader, live_successor, spec_facts, strict_imports
from mu_spec.graph import Graph
from mu_spec.identifiers import SPEC, TEST, Identifier, InvalidIdentifier, parse, sort_key
from mu_spec.storage import CROSS_CUTTING, Manifest

DEPENDENCY_CYCLE = "dependency_cycle"
CROSS_CUTTING_OUTBOUND = "cross_cutting_outbound"
BAD_EMISSION = "bad_emission"
# An emission whose target was RETIRED rather than broken. Reported, never
# refused: an emission consumes nothing, so a successor on the other end
# changes nothing the emitter relied on. See the branch that raises it.
STALE_EMISSION = "stale_emission"
OVERLAPPING_MEMBERSHIP = "overlapping_membership"
# `module_gates`: read against the module map, all three blocking.
BAD_MODULE_IMPORT = "bad_module_import"
SPLIT_OWNERSHIP = "split_ownership"
UNDECLARED_PATH = "undeclared_path"


@dataclasses.dataclass(frozen=True)
class SliceFinding:
    kind: str
    slice: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.slice} -- {self.detail}"


def _reaches(edges: dict[str, tuple[str, ...]], start: str) -> set[str]:
    """Every slice reachable from `start`, following dependencies."""
    seen: set[str] = set()
    queue = list(edges.get(start, ()))
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(edges.get(current, ()))
    return seen


def cycles(edges: dict[str, tuple[str, ...]]) -> list[list[str]]:
    """Every group of slices that mutually depend on each other.

    Two slices are in the same cycle when each reaches the other. Groups are
    returned once, named in sorted order, so one cycle is one finding rather
    than one per member.

    ponytail: reachability per node, O(n * (n + e)). n is the number of
    slices in one project -- tens, not thousands. Swap in Tarjan if a project
    ever gets big enough for this to show up.
    """
    reach = {name: _reaches(edges, name) for name in edges}
    grouped: set[str] = set()
    found: list[list[str]] = []
    for name in sorted(edges):
        if name in grouped:
            continue
        group = sorted(
            other
            for other in reach[name]
            if other != name and name in reach.get(other, set())
        )
        if not group:
            continue
        members = sorted([name, *group])
        grouped.update(members)
        found.append(members)
    return found


def _path(edges: dict[str, tuple[str, ...]], members: list[str]) -> str:
    """A readable route through a cycle, so the finding names the loop rather
    than just the slices caught in it."""
    inside = set(members)
    start = members[0]
    route = [start]
    current = start
    while True:
        nxt = next((d for d in sorted(edges.get(current, ())) if d in inside), None)
        if nxt is None:
            break
        route.append(nxt)
        if nxt == start:
            break
        if route.count(nxt) > 1:
            break
        current = nxt
    return " -> ".join(route)


def edge_gates(manifest: Manifest, graph: Graph) -> list[Finding]:
    """The entry-level checks that need to know what kind of slice an edge
    lands in. Blocking, like every other soundness check.

    Two rules, and they are the same rule seen from both ends -- an edge into
    a cross-cutting slice is an emission, and an emission goes nowhere else.

    - **`depends_on` may not point into a cross-cutting slice** from outside
      it. Depending on
      something means branching on what it gives you back, and a concern
      whose answer you branch on fails the first classification test. The two
      claims cannot both be true, so the edge is a contradiction rather than
      a style choice. The fix is to state it as an emission.
    - **`emits_into` may only point into a cross-cutting slice**, in the same
      layer, at a live entry. Emitting into an ordinary slice would be a
      dependency with the ordering quietly filed off.
    """
    findings: list[Finding] = []
    cross = set(manifest.cross_cutting())

    for entry in graph.entries():
        problems: list[str] = []
        stale: list[str] = []

        home = manifest.slice_of(entry.id)
        for target in entry.depends_on:
            owner = manifest.slice_of(target)
            # Within the concern there is no caller to branch, only build
            # order: an entry of the slice may need another of the same slice.
            if owner is not None and owner in cross and owner != home:
                # Two claims that cannot both hold, and the message must
                # not assume which one is false. If the dependency is
                # spurious, the edge is an emission. If it is real -- the
                # caller consumes what the target returns -- then the
                # classification is what is wrong, and no relabelling will
                # express it: fan-in is not the criterion, and a slice
                # others depend on is FOUNDATIONAL rather than
                # cross-cutting. A slice holding both an ambient concern and
                # a service its callers consume is two slices, and slices
                # split.
                problems.append(
                    f"{target} is in {owner!r}, which is cross-cutting, and "
                    "a cross-cutting slice is reached with emits_into. Two "
                    "readings, and they have opposite remedies. If nothing "
                    "is consumed back, this is an emission -- change the "
                    "edge. If the caller really does use what "
                    f"{target} returns, then {owner!r} is misclassified for "
                    "this entry: depending on it is what a foundational "
                    "slice is for, and fan-in is never what makes something "
                    f"cross-cutting. Split {owner!r} and classify each half"
                )

        for target in entry.emits_into:
            owner = manifest.slice_of(target)
            if target == entry.id:
                problems.append(f"{target} emits into itself")
            elif target.layer != entry.id.layer:
                problems.append(
                    f"{target} is not in the same layer -- an emission is "
                    "horizontal, like a dependency"
                )
            elif graph.superseded_by(target) is not None:
                # Reported, never refused, and this is the one place the
                # emission rule parts company with the dependency rule.
                #
                # A dependency on a retired entry blocks because the
                # dependent CONSUMED a meaning that has since moved. An
                # emission consumes nothing -- that is the whole definition,
                # and it is why an emission imposes no order and why a
                # concern is derivable before everything that emits into it.
                # If nothing came back, nothing moved for the emitter.
                #
                # The test that settles it: an emitter that needs to know
                # the target's shape has declared the wrong edge kind.
                # Needing what is on the other end is what makes something a
                # dependency. So either the emission is true and the
                # supersession is irrelevant to it, or the edge was
                # mislabelled and the repair is the edge rather than this
                # gate. Refusing here was the dependency rule inherited
                # rather than argued.
                #
                # A target that never existed is still refused, below. That
                # one is a dangling edge; this one is a retired entry whose
                # successor the graph can name.
                stale.append(
                    f"{target} is superseded by {graph.superseded_by(target)}"
                )
            elif target not in graph:
                problems.append(f"{target} does not exist")
            elif owner is None or owner not in cross:
                problems.append(
                    f"{target} is not cross-cutting -- an emission goes into "
                    "a concern and is never consumed back. Into an ordinary "
                    "slice it would be a dependency with the ordering filed "
                    "off"
                )

        # Two kinds, because one entry can hold both a broken emission and a
        # merely stale one, and collapsing them would make the blocking half
        # invisible behind the reported half.
        if problems:
            findings.append(Finding(BAD_EMISSION, entry.id, "; ".join(problems)))
        if stale:
            findings.append(Finding(STALE_EMISSION, entry.id, "; ".join(stale)))

    return sorted(findings, key=lambda f: (sort_key(f.id), f.kind))


def code_roots(manifest: Manifest) -> set[str]:
    """The top-level directories the project's spec modules live under
    (`dark`, `configs`). A path or a dotted import outside them is not this
    project's code, so nothing here checks it. A bare filename
    (`pyproject.toml`) has no directory and is out of scope.

    A directory holding test modules is not a root even when a spec module
    sits there too: a test file is declared against test entries, which are
    written after the spec naming the file, and no spec entry may claim it
    (a module implements spec entries or test entries, never both)."""
    def top(layer):
        return {
            p.split("/")[0] for p, ids in manifest.modules.items()
            if "/" in p and any(i.layer == layer for i in ids)
        }
    return top(SPEC) - top(TEST)


def _module_files(dotted: str) -> tuple[str, str]:
    """`dark.x.y` is written either as `dark/x/y.py` or as a package."""
    base = dotted.replace(".", "/")
    return f"{base}.py", f"{base}/__init__.py"


def module_gates(
    manifest: Manifest, graph: Graph, read: Reader = strict_imports
) -> list[Finding]:
    """The checks that read spec bodies against the module map.
    `bad_module_import` and `undeclared_path` block (`sound`), so a cut is
    refused while one stands; `split_ownership` is reported only (10-24,
    see `service._BLOCKING`).

    - **`bad_module_import` (M2).** An `Imports:` item naming a dotted module
      under a code root must name a declared module, and the entry must
      depend on a spec entry that module implements (or be one). Otherwise
      the dependency exists in the code and nowhere the scheduler can see.
    - **`split_ownership` (M3).** A module implemented by spec entries of two
      slices has two owners, and slices define write ownership. Ordinary
      overlap -- several units of ONE slice writing a file -- stays the
      report in `units.overlap`; this is only the cross-slice subset of it.
      A test module counts too, each test entry in the slice of the contract
      it judges: DARK's test_swarm.py held ~19 mutually exclusive test units
      across slices (retro pattern muspec-spec-03).
    - **`undeclared_path` (M3).** A spec body naming a file under a code root
      that no module declares. Whoever writes that file owns it without a
      backlink, so nothing orders or audits the write.

    Not run on the amendment path: a module is declared only after the entry
    it implements exists (`declare_module` checks the id is live), so a spec
    naming its own file would be refused before it could be declared. The
    module map's write is the moment these become answerable; the cut is
    where they refuse.
    """
    findings: list[Finding] = []
    roots = code_roots(manifest)
    live = {i for ids in manifest.modules.values() for i in ids if i in graph}

    def slice_of(i: Identifier) -> str | None:
        # A test entry's slice is the slice of the contract it judges.
        if i.layer == TEST:
            parents = graph.parents(i)
            return manifest.slice_of(parents[0]) if parents else None
        return manifest.slice_of(i)

    for path, ids in sorted(manifest.modules.items()):
        owners: dict[str, list[Identifier]] = {}
        for i in sorted(ids & live, key=sort_key):
            name = slice_of(i)
            if name is not None:
                owners.setdefault(name, []).append(i)
        if len(owners) > 1:
            who = "; ".join(
                f"{', '.join(map(str, members))} ({name})"
                for name, members in sorted(owners.items())
            )
            for members in owners.values():
                for i in members:
                    findings.append(Finding(
                        SPLIT_OWNERSHIP, i,
                        f"{path} is implemented from {len(owners)} slices: {who}. "
                        "A file has one owning slice -- split it so each slice "
                        "declares its own module, or move the entries into one "
                        "slice",
                    ))

    for entry in graph.entries():
        if entry.id.layer != SPEC:
            continue
        problems: list[str] = []
        for item in read(entry) or ():
            if _is_id(item.target) or item.target.split(".")[0] not in roots:
                continue
            files = [f for f in _module_files(item.target) if f in manifest.modules]
            names = ", ".join(item.names)
            if not files:
                problems.append(
                    f"{item.target} is not a declared module -- nothing declares "
                    f"{' or '.join(_module_files(item.target))}. Name the entry "
                    f"instead (`S-NN {names}`), or declare the module"
                )
                continue
            implementers = {i for f in files for i in manifest.modules[f]}
            reachable = implementers | {live_successor(graph, i) for i in implementers}
            # An edge into a since-retired implementer still names the module;
            # that it is retired is `bad_dependencies`' finding, not this one.
            needs = set(entry.depends_on) | {live_successor(graph, d) for d in entry.depends_on}
            if entry.id not in reachable and not reachable & needs:
                problems.append(
                    f"imports {names} from {item.target}, which "
                    f"{', '.join(sorted(map(str, implementers), key=str))} "
                    "implement, but depends on none of them -- add the one "
                    "that defines these names to depends_on"
                )
        undeclared = [
            p for p in spec_facts(entry.body if isinstance(entry.body, str) else "").path_mentions
            # A file, not a directory: ownership is per file, and a directory
            # is declared only through the files in it.
            if "/" in p and not p.endswith("/") and p.split("/")[0] in roots
            and p not in manifest.modules and _asserted(entry.body, p)
        ]
        if undeclared:
            findings.append(Finding(
                UNDECLARED_PATH, entry.id,
                f"names {', '.join(undeclared)}, which no module declares -- "
                "declare it with the spec entry that writes it, or drop the path",
            ))
        if problems:
            findings.append(Finding(BAD_MODULE_IMPORT, entry.id, "; ".join(problems)))

    return sorted(findings, key=lambda f: (sort_key(f.id), f.kind))


# ponytail: a word-level negation test, not a parse. It catches "there is no
# `dark/report/runtime.py`" (DARK S-121), and a negation phrased further from
# the path ("a file like X is never written") still counts as a mention.
_NEGATED = re.compile(r"\b(?:no|not|never|without)\s+`?$", re.IGNORECASE)


def _asserted(text: str, path: str) -> bool:
    """Some mention of `path` is not negated -- a body naming a file only to
    say it must not exist claims nothing about who writes it."""
    return any(
        not _NEGATED.search(text[max(0, m.start() - 16):m.start()])
        for m in re.finditer(re.escape(path), text)
    )


def _is_id(text: str) -> bool:
    try:
        parse(text)
    except InvalidIdentifier:
        return False
    return True


def slice_gates(manifest: Manifest, graph: Graph) -> list[SliceFinding]:
    """Every slice-level finding, in kind then name order."""
    edges = manifest.dependency_graph(graph)
    findings: list[SliceFinding] = []

    # One entry belongs to exactly one slice. Overlapping membership is no
    # ownership: two work packages would hand the same entry out as editable,
    # two agents would edit it, and the audit would pass for both -- which
    # makes it blind to the one thing that actually broke.
    owners: dict[str, list[str]] = {}
    for name, sl in sorted(manifest.slices.items()):
        for member in sl.members:
            owners.setdefault(str(member), []).append(name)
    for member, names in sorted(owners.items()):
        if len(names) > 1:
            findings.append(
                SliceFinding(
                    OVERLAPPING_MEMBERSHIP,
                    names[0],
                    f"{member} is a member of {', '.join(names)}. An entry "
                    "belongs to exactly one slice -- slices define write "
                    "ownership, and two owners is no owner",
                )
            )

    for name in manifest.cross_cutting():
        reaching_out = [
            dep
            for dep in edges.get(name, ())
            if manifest.slices[dep].type != CROSS_CUTTING
        ]
        if reaching_out:
            findings.append(
                SliceFinding(
                    CROSS_CUTTING_OUTBOUND,
                    name,
                    f"{name} is cross-cutting but depends on "
                    f"{', '.join(reaching_out)} -- a concern that has to ask a "
                    "feature slice for something needs to know its caller, "
                    "which is what its classification says it does not",
                )
            )

    for members in cycles(edges):
        findings.append(
            SliceFinding(
                DEPENDENCY_CYCLE,
                members[0],
                f"{_path(edges, members)} -- these slices need each other, so "
                "none of them can be derived first. The cut is wrong: pull the "
                "shared part out into a slice they both depend on",
            )
        )

    return sorted(findings, key=lambda f: (f.kind, f.slice))
