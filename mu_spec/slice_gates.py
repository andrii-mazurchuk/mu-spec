"""The two mechanical checks that need the manifest.

`gates.py` deliberately knows nothing about slices -- it answers questions
about entries and their edges. These two are about the *projected* slice
graph, so they need to know which slice owns which identifier, and which
slices are cross-cutting. They live here rather than there for that reason.

Both are **blocking**, and that is a decision rather than a default. A cycle
and a cross-cutting slice reaching into a feature slice are not conditions
you propagate through and tidy up afterwards -- they mean the cut is wrong,
and every entry derived while they stand is derived against a structure that
does not hold. Blocking early is cheap: the fix is to reslice, and reslicing
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

There is no judgement here, and never will be. Whether a slice *is*
cross-cutting is a call an agent argues and a human rules on. Once that
ruling is recorded, whether the edges are legal is arithmetic.
"""

from __future__ import annotations

import dataclasses

from mu_spec.graph import Graph
from mu_spec.storage import CROSS_CUTTING, Manifest

DEPENDENCY_CYCLE = "dependency_cycle"
CROSS_CUTTING_OUTBOUND = "cross_cutting_outbound"


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


def slice_gates(manifest: Manifest, graph: Graph) -> list[SliceFinding]:
    """Every slice-level finding, in kind then name order."""
    edges = manifest.dependency_graph(graph)
    findings: list[SliceFinding] = []

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
