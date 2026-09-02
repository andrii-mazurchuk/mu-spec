"""Admission gates: the mechanical checks a graph must pass.

From the design doc: "mechanical, run by the agent, human sees only
failures." A mechanical check does not need an agent -- it needs a function.
Keeping them here, in the unit, is what stops a session in a hurry from
skipping them, which is the entire point of a gate.

Two of the three live here, because both are answerable from the graph
alone:

- orphans -- does every entry below trace to something above?
- unserved -- does every entry above have at least one entry below serving it?

The third, backwards dependency arrows between slices, needs the manifest
(which slice owns which identifiers, and which dependencies are declared).
It lands with the manifest, not here -- this module deliberately knows
nothing about slices.

Judgement gates are not here and never will be: an agent flagging what it
could not derive is not a computation.
"""

from __future__ import annotations

import dataclasses

from mu_spec.graph import Graph
from mu_spec.identifiers import Identifier, is_upward, sort_key

ORPHAN = "orphan"
UNSERVED = "unserved"


@dataclasses.dataclass(frozen=True)
class Finding:
    kind: str
    id: Identifier
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.id} -- {self.detail}"


def _top_layer_depth(graph: Graph) -> int:
    """The shallowest depth actually present. Entries at it derive from
    nothing by definition and can never be orphans.

    Computed from the graph rather than hardcoded to intent's depth, so a
    project whose graph legitimately starts at a lower layer -- a slice
    extracted for review, a partial load -- doesn't come back solid red.
    """
    entries = graph.entries()
    return min((e.id.depth for e in entries), default=0)


def _bottom_layer_depth(graph: Graph) -> int:
    """The deepest depth present. Entries at it are served by code, which is
    tracked by module backlinks rather than by entries in this graph, so
    nothing here can serve them and they are never unserved."""
    entries = graph.entries()
    return max((e.id.depth for e in entries), default=0)


def orphans(graph: Graph) -> list[Finding]:
    """An entry below the top layer must trace to something above it: at
    least one derives-from edge that resolves to a live entry and runs
    upward. One good parent is enough -- but a bad edge alongside it is
    still reported, because a dangling or backwards reference is a defect
    someone has to fix and must not vanish behind a valid sibling."""
    top = _top_layer_depth(graph)
    findings: list[Finding] = []

    for entry in graph.entries():
        if entry.id.depth == top:
            continue

        if not entry.derives_from:
            findings.append(
                Finding(ORPHAN, entry.id, "derives from nothing")
            )
            continue

        problems: list[str] = []
        good = 0
        for parent in entry.derives_from:
            if not is_upward(entry.id, parent):
                problems.append(f"{parent} is not upward of {entry.id}")
            elif graph.superseded_by(parent) is not None:
                problems.append(
                    f"{parent} is superseded by {graph.superseded_by(parent)}"
                )
            elif parent not in graph:
                problems.append(f"{parent} does not exist")
            else:
                good += 1

        if good == 0 or problems:
            findings.append(Finding(ORPHAN, entry.id, "; ".join(problems)))

    return findings


def unserved(graph: Graph) -> list[Finding]:
    """An entry above the bottom layer must have at least one live entry
    deriving from it. A requirement nothing serves is a requirement nobody
    built."""
    bottom = _bottom_layer_depth(graph)
    return [
        Finding(UNSERVED, entry.id, "nothing derives from it")
        for entry in graph.entries()
        if entry.id.depth != bottom and not graph.children(entry.id)
    ]


def admission_gates(graph: Graph) -> list[Finding]:
    """Every mechanical finding, in spine order -- the human sees only
    failures, so this list is the whole report and should read top-down the
    way the graph does."""
    findings = orphans(graph) + unserved(graph)
    return sorted(findings, key=lambda f: (sort_key(f.id), f.kind))
