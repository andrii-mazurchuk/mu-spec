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

Keeping those apart matters, because they have different consequences.
Unsound blocks: an amendment that would introduce an orphan is refused, and
no work package is issued from a graph containing one. Incomplete does not
block -- it is the report of what is left to do.

Cycles between slices, and the rule that a cross-cutting slice holds no
outbound dependency, both need the manifest -- which slice owns which
identifiers. They land with the manifest, not here: this module deliberately
knows nothing about slices.

Judgement gates are not here and never will be: an agent flagging what it
could not derive is not a computation.
"""

from __future__ import annotations

import dataclasses

from mu_spec.graph import Graph
from mu_spec.identifiers import LAYERS, Identifier, derives_legally, sort_key

ORPHAN = "orphan"
UNSERVED = "unserved"
BAD_DEPENDENCY = "bad_dependency"


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
        good = 0
        for parent in entry.derives_from:
            if not derives_legally(entry.id, parent):
                problems.append(
                    f"{entry.id} must derive from the layer directly above; "
                    f"{parent} is not there"
                )
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
    built.

    Only a legal child counts. An entry two layers down claiming to serve
    this one is an illegal edge, and if it silenced this gate you could
    satisfy any requirement by jumping the layers that explain how -- the
    orphan gate would object, but the requirement would read as met.
    """
    return [
        Finding(UNSERVED, entry.id, "nothing derives from it")
        for entry in graph.entries()
        if entry.id.depth != _BOTTOM_DEPTH
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


def admission_gates(graph: Graph) -> list[Finding]:
    """Every mechanical finding, in spine order -- the human sees only
    failures, so this list is the whole report and should read top-down the
    way the graph does."""
    findings = orphans(graph) + bad_dependencies(graph) + unserved(graph)
    return sorted(findings, key=lambda f: (sort_key(f.id), f.kind))
