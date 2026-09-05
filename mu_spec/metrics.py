"""The numbers, and nothing that acts on them.

Two rules govern this module, and both are load-bearing:

**Nothing here ever gates.** Every existing gate blocks on something
*definitionally* broken -- a cycle, a dangling edge, an entry with two
owners. Everything here is a *proxy* for a question nobody can answer yet
("is this slicing any good"), and baking a proxy into the one place the
system is supposed to be certain would be the worst trade in the design.
These are reported. They never refuse.

**Nothing here is a verdict.** A slicing that scores badly might be right,
and the unit has no way to know. Callers get `verdict_inputs`; the judgement
belongs to whoever is reading.

The metrics split by when their data exists, which is the whole difficulty:

- *Ex ante*, at slicing time, only intent and behaviour exist. Parent
  exclusivity and size are all there is -- enough to filter an obviously bad
  proposal, not enough to pick the best one. Stated plainly rather than
  dressed up.
- *Structural*, once a layer has propagated: cohesion, coupling, wave shape.
  Real, and they arrive while a split is still cheap.
- *Retrospective*, from history: change locality and the correction
  distribution. The only measurements here that are not proxies.

Change locality is the primary one. A good slicing is one where a typical
change lands inside a single slice -- that is the definition, not a stand-in
for it, and every request already records exactly which entries it produced.
"""

from __future__ import annotations

from mu_spec.graph import Graph
from mu_spec.identifiers import InvalidIdentifier, parse
from mu_spec.inbox import Inbox
from mu_spec.storage import Manifest

# Which layer a correction entered at. The distribution of these over time is
# `DESIGN.md` §9's debug signal for the pipeline itself: clustered at intent
# means the interview was too shallow to derive from; clustered lower means
# the derivation prompting is weak.
CORRECTION_TYPES = ("initiate", "feature", "correction")


def change_locality(manifest: Manifest, inbox: Inbox, project: str) -> dict:
    """How many slices each completed change had to touch.

    One is perfect. Anything higher is the slicing failing to contain a
    change it should have contained -- which is the definition of a bad cut,
    measured rather than approximated.

    Entries with no slice (intent is never sliced) are ignored rather than
    counted as a slice of their own: every change touches intent, so counting
    it would add one to every score and tell you nothing.
    """
    changes = []
    for message in inbox.list(project=project):
        produced = (message.resolution or {}).get("produced", [])
        slices = set()
        for raw in produced:
            try:
                owner = manifest.slice_of(parse(str(raw)))
            except InvalidIdentifier:
                continue  # "project:name" and similar bookkeeping refs
            if owner is not None:
                slices.add(owner)
        if slices:
            changes.append(
                {
                    "message": message.id,
                    "type": message.type,
                    "slices": sorted(slices),
                    "count": len(slices),
                }
            )

    counts = [c["count"] for c in changes]
    distribution: dict[str, int] = {}
    for n in counts:
        distribution[str(n)] = distribution.get(str(n), 0) + 1

    return {
        "changes": len(changes),
        "single_slice": sum(1 for n in counts if n == 1),
        "mean": round(sum(counts) / len(counts), 2) if counts else None,
        "distribution": dict(sorted(distribution.items())),
        # Named, because "your slicing scores 2.4" is useless and "msg-0007
        # had to touch discovery, payouts and audit" is a thing you can go
        # and look at.
        "worst": sorted(changes, key=lambda c: -c["count"])[:5],
    }


def corrections_by_layer(graph: Graph, inbox: Inbox, project: str) -> dict:
    """Where corrections entered, per `DESIGN.md` §9.

    A correction is a request that superseded something. The layer of the
    *topmost* entry it produced is where the defect was classified to, and
    that classification is the diagnostic -- once the fix has propagated, the
    graph just looks correct and the evidence is gone.
    """
    by_layer: dict[str, int] = {}
    total = 0
    for message in inbox.list(project=project, kind="correction"):
        produced = (message.resolution or {}).get("produced", [])
        depths = []
        for raw in produced:
            try:
                depths.append(parse(str(raw)))
            except InvalidIdentifier:
                continue
        if not depths:
            continue
        topmost = min(depths, key=lambda i: i.depth)
        by_layer[topmost.layer_name] = by_layer.get(topmost.layer_name, 0) + 1
        total += 1

    return {
        "corrections": total,
        "by_layer": dict(sorted(by_layer.items())),
        "reading": (
            "clustered at intent means the interview was too shallow to "
            "derive from; clustered lower means the derivation prompting is "
            "weak. A distribution, not a threshold -- nothing here gates."
        ),
    }


def structural(manifest: Manifest, graph: Graph) -> dict:
    """Cohesion and coupling per slice, from the edges that actually exist.

    Cohesion is the share of a slice's dependency edges that stay inside it.
    A slice whose edges nearly all point outward is not a slice, it is a
    layer of indirection someone drew a box around.

    Emissions are excluded from both. They impose no order and cross into a
    concern by design, so counting them as coupling would penalise exactly
    the thing the edge exists to make cheap.
    """
    rows = []
    for name, sl in sorted(manifest.slices.items()):
        internal = outbound = 0
        emissions = 0
        for entry in graph.entries():
            if entry.id not in sl.members:
                continue
            for target in entry.depends_on:
                owner = manifest.slice_of(target)
                if owner == name:
                    internal += 1
                elif owner is not None:
                    outbound += 1
            emissions += len(entry.emits_into)

        edges = internal + outbound
        inbound = sum(
            1
            for entry in graph.entries()
            if manifest.slice_of(entry.id) not in (name, None)
            for target in entry.depends_on
            if target in sl.members
        )
        rows.append(
            {
                "slice": name,
                "type": sl.type,
                "size": len(sl.members),
                "internal_edges": internal,
                "outbound_edges": outbound,
                "inbound_edges": inbound,
                "emissions": emissions,
                "cohesion": round(internal / edges, 2) if edges else None,
                "depends_on": list(manifest.dependency_graph(graph).get(name, ())),
            }
        )
    return {"slices": rows}
