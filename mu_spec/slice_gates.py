"""The mechanical checks that need the manifest.

`gates.py` deliberately knows nothing about slices -- it answers questions
about entries and their edges alone. Everything here needs to know which
slice owns which identifier, so it lives here instead. The slice checks are
about the *projected* slice graph and name a slice.

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

- **An entry in more than one slice.** Slices define write ownership, and
  overlapping ownership is no ownership: two work packages would hand the
  same entry out as editable, and the audit would pass for both.

There is no judgement here, and never will be.
"""

from __future__ import annotations

import dataclasses
import re

from mu_spec.gates import Finding, Reader, live_successor, spec_facts, strict_imports
from mu_spec.graph import Graph
from mu_spec.identifiers import SPEC, TEST, Identifier, InvalidIdentifier, parse, sort_key
from mu_spec.storage import Manifest

DEPENDENCY_CYCLE = "dependency_cycle"
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
