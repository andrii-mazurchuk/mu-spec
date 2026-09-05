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

from mu_spec.gates import Finding
from mu_spec.graph import Graph
from mu_spec.identifiers import sort_key
from mu_spec.storage import CROSS_CUTTING, Manifest

DEPENDENCY_CYCLE = "dependency_cycle"
CROSS_CUTTING_OUTBOUND = "cross_cutting_outbound"
BAD_EMISSION = "bad_emission"
OVERLAPPING_MEMBERSHIP = "overlapping_membership"


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


def _cycles(edges: dict[str, tuple[str, ...]]) -> list[list[str]]:
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

    - **`depends_on` may not point into a cross-cutting slice.** Depending on
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

        for target in entry.depends_on:
            owner = manifest.slice_of(target)
            if owner is not None and owner in cross:
                problems.append(
                    f"{target} is in {owner!r}, which is cross-cutting -- "
                    "reach it with emits_into, not depends_on. Depending on "
                    "it means branching on what it returns, and a concern "
                    "you branch on is not cross-cutting"
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
                problems.append(
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

        if problems:
            findings.append(Finding(BAD_EMISSION, entry.id, "; ".join(problems)))

    return sorted(findings, key=lambda f: sort_key(f.id))


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

    for members in _cycles(edges):
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
