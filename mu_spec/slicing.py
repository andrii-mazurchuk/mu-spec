"""Proposing a slicing, and scoring one before it exists.

Slicing is the one decision in this pipeline that is expensive to undo.
Slices split but never merge, so a cut that turns out wrong can be subdivided
forever and never put back together. Everything here exists to move the
argument earlier, to the point where the remedy is still free.

Two things, both read-only:

**Candidates.** The graph half of the grouping problem: which behaviours
share an intent parent, and which intent entries spread across many
candidates. Deliberately *not* the text half. The design doc's first step is
"group by shared nouns", and that stays the agent's job -- it reads the
bodies. Counting token overlap here would be the retriever-scored-it-0.83
problem in different clothing: tunable, unexplainable, and needing a
tokenizer this unit does not have. "These group because they share intent
parent I·03" is a sentence somebody can check.

**Scoring.** Take a proposed partition, commit nothing, and run every gate
and every structural metric against it as though it were real. This is what
makes trialling several slicings possible at all -- and it is where the
decision to hard-block conflicts pays off, because a cycle caught here costs
a rename, while the same cycle caught after ratification costs a split.

Nothing here gates and nothing here is a verdict. A proposal that scores
badly may still be the right cut; the numbers are for arguing with.
"""

from __future__ import annotations

from mu_spec.graph import Graph
from mu_spec.identifiers import InvalidIdentifier, Identifier, parse, sort_key
from mu_spec.metrics import structural
from mu_spec.slice_gates import edge_gates, slice_gates
from mu_spec.storage import SLICE, Manifest, Slice
from mu_spec.waves import schedule

BEHAVIOUR = "B"


def candidates(graph: Graph, layer: str = BEHAVIOUR) -> dict:
    """The structural raw material for grouping.

    Two signals, and they are the only ones available before architecture
    exists:

    - **shared parentage** -- entries deriving from the same entry above are
      usually about the same thing. The strongest ex-ante signal there is,
      and it is free because the edge is already in the graph.
    - **spread** -- a parent whose children scatter widely is either
      constraint-shaped ("every action must be auditable", a cross-cutting
      tell) or evidence a cut runs across the grain of intent. Two opposite
      readings from one number, which is exactly why it is reported for an
      agent to argue from rather than acted on.
    """
    entries = [e for e in graph.entries() if e.id.layer == layer]

    by_parent: dict[str, list[str]] = {}
    for entry in entries:
        for parent in entry.derives_from:
            by_parent.setdefault(str(parent), []).append(str(entry.id))

    pairs: list[dict] = []
    for parent, children in by_parent.items():
        for i, a in enumerate(sorted(children)):
            for b in sorted(children)[i + 1 :]:
                existing = next(
                    (p for p in pairs if p["pair"] == [a, b]), None
                )
                if existing:
                    existing["shared_parents"].append(parent)
                else:
                    pairs.append({"pair": [a, b], "shared_parents": [parent]})

    return {
        "layer": layer,
        "entries": [
            {
                "id": str(e.id),
                "title": e.display_title,
                "derives_from": [str(d) for d in e.derives_from],
            }
            for e in entries
        ],
        "parents": [
            {"id": parent, "children": sorted(children), "fan_out": len(children)}
            for parent, children in sorted(by_parent.items())
        ],
        "shared_parentage": sorted(
            pairs, key=lambda p: (-len(p["shared_parents"]), p["pair"])
        ),
        "note": "grouping by shared nouns is the reading agent's job -- this "
        "is the graph half only",
    }


def _parse_proposal(proposal: dict, graph: Graph) -> dict[str, set[Identifier]]:
    if not isinstance(proposal, dict) or not proposal:
        raise ValueError("'proposal' must be a non-empty object of slice -> ids")
    out: dict[str, set[Identifier]] = {}
    seen: dict[Identifier, str] = {}
    for name, ids in proposal.items():
        if not isinstance(ids, list):
            raise ValueError(f"{name!r}: members must be a list of identifiers")
        members: set[Identifier] = set()
        for raw in ids:
            try:
                identifier = parse(str(raw))
            except InvalidIdentifier as exc:
                raise ValueError(f"{name!r}: {exc}") from exc
            # `get`, not `in`: a superseded entry is still a member of the
            # slice it was born in, and membership is never cleaned up when
            # something is retired. Refusing them would make it impossible
            # to score a project's *current* slicing the moment it has taken
            # a single correction.
            if graph.get(identifier) is None:
                raise ValueError(f"{name!r}: {identifier} does not exist")
            if identifier in seen:
                raise ValueError(
                    f"{identifier} is in both {seen[identifier]!r} and "
                    f"{name!r}; an entry belongs to exactly one slice"
                )
            seen[identifier] = name
            members.add(identifier)
        out[name] = members
    return out


def score(
    manifest: Manifest, graph: Graph, proposal: dict, types: dict | None = None
) -> dict:
    """Run every check and every metric against a partition that does not
    exist yet.

    Nothing is written. The manifest handed in is used only for its
    identifier bookkeeping; membership comes entirely from the proposal, so
    a project with slices already can still be scored against a different
    cut -- which is what replaying a change history under an alternative
    slicing requires.
    """
    members = _parse_proposal(proposal, graph)
    types = types or {}

    prospective = Manifest(
        project=manifest.project,
        slices={
            name: Slice(
                name=name, members=ids, type=types.get(name, SLICE)
            )
            for name, ids in members.items()
        },
        allocation=dict(manifest.allocation),
    )

    findings = slice_gates(prospective, graph)
    entry_findings = edge_gates(prospective, graph)
    sched = schedule(prospective, graph)
    metrics = structural(prospective, graph)

    unassigned = [
        str(e.id)
        for e in graph.entries()
        if e.id.layer != "I" and prospective.slice_of(e.id) is None
    ]

    warnings = []
    for row in metrics["slices"]:
        if row["size"] == 1:
            warnings.append(
                f"{row['slice']}: one entry — probably not a slice of its own"
            )
        if row["cohesion"] == 0.0 and row["outbound_edges"]:
            warnings.append(
                f"{row['slice']}: every dependency points outward — a layer of "
                "indirection with a box drawn around it, not a slice"
            )
    if sched.chain:
        warnings.append(
            "every wave is one slice wide — nothing can be worked in "
            "parallel, so the slices are probably too coupled"
        )
    if unassigned:
        warnings.append(
            f"{len(unassigned)} entries are in no slice — they would have "
            "nowhere to live"
        )

    return {
        "legal": not findings and not entry_findings,
        "slice_findings": [
            {"kind": f.kind, "slice": f.slice, "detail": f.detail}
            for f in findings
        ],
        "findings": [
            {"kind": f.kind, "id": str(f.id), "detail": f.detail}
            for f in entry_findings
        ],
        **metrics,
        "waves": [
            {"wave": n, "slices": list(w), "width": len(w)}
            for n, w in enumerate(sched.waves)
        ],
        "unschedulable": list(sched.unschedulable),
        "chain": sched.chain,
        "unassigned": sorted(unassigned, key=lambda s: sort_key(parse(s))),
        "warnings": warnings,
        # Never a verdict. Legal and good are different questions, and the
        # unit can only answer the first one.
        "note": "legal is computed; good is not. These are inputs to a "
        "judgement, and nothing here gates or refuses anything",
    }
