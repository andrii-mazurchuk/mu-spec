"""Execution order, computed rather than chosen.

A slice's wave is the **longest** path from it to a slice that depends on
nothing. Longest, not shortest, and that is the whole trick: it guarantees a
slice is scheduled strictly after everything it needs, however long the
longest chain beneath it happens to be. Shortest-path would put a slice in
the same wave as something it depends on the moment a second, longer route
existed.

Two properties fall out of that, and both are why this is worth computing at
all rather than ordering by hand:

- **Two slices in the same wave have no edge between them.** Not usually --
  structurally. If A depends on B then A's longest path is at least one
  longer than B's, so they cannot land together. Agents working the same wave
  therefore never need to talk to each other, and there is nothing to lock.
- **Every wave below is finished before the next begins**, so a wave-N agent
  reads its dependencies as frozen artifacts. No coordination, no
  consistency problem.

A cross-cutting slice lands in wave 0 by construction, because the edge rules
leave it no outbound dependency to have. That is not arranged here; it falls
out, which is a useful sign the edge rules are doing real work.

Cycles are not a scheduling problem to be worked around -- they are an
admission failure, and `slice_gates` refuses them. This module still has to
survive one without hanging, because a gate can only report on a structure it
can walk, so anything caught in a cycle comes back as `unschedulable`.
"""

from __future__ import annotations

import dataclasses

from mu_spec.graph import Graph
from mu_spec.storage import Manifest


@dataclasses.dataclass(frozen=True)
class Schedule:
    # Wave 0 first. Slices within a wave are sorted, and unordered in
    # practice -- there are no edges between them to order by.
    waves: tuple[tuple[str, ...], ...] = ()
    # Slices caught in a cycle, so they have no longest path to a root. Empty
    # whenever the admission gates pass.
    unschedulable: tuple[str, ...] = ()

    def wave_of(self) -> dict[str, int]:
        return {
            name: number
            for number, wave in enumerate(self.waves)
            for name in wave
        }

    @property
    def chain(self) -> bool:
        """Every wave one slice wide means the dependency graph is a chain and
        nothing can be done in parallel. A signal that the slices are too
        coupled -- reported, never acted on."""
        return len(self.waves) > 1 and all(len(w) == 1 for w in self.waves)


def schedule(manifest: Manifest, graph: Graph) -> Schedule:
    """Assign every slice to a wave.

    Relaxation rather than a recursive walk: a slice becomes assignable once
    every slice it depends on is assigned, and its wave is one past the
    deepest of them. When a pass assigns nothing and slices remain, those
    remaining are exactly the ones in a cycle -- which is why this terminates
    on a broken graph instead of recursing forever.
    """
    edges = manifest.dependency_graph(graph)
    assigned: dict[str, int] = {}
    pending = set(edges)

    while pending:
        ready = {
            name
            for name in pending
            if all(dep in assigned for dep in edges[name])
        }
        if not ready:
            break
        for name in ready:
            deps = edges[name]
            assigned[name] = 1 + max((assigned[d] for d in deps), default=-1)
        pending -= ready

    depth = max(assigned.values(), default=-1)
    waves = tuple(
        tuple(sorted(n for n, w in assigned.items() if w == number))
        for number in range(depth + 1)
    )
    return Schedule(waves=waves, unschedulable=tuple(sorted(pending)))
