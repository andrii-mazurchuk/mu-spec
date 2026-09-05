"""Routing the issue queue into repair batches.

The router's job is deliberately small, and its smallness is the point: it
reads **issue headers only** -- target, requester, kind, one-line claim,
about thirty tokens each. It never opens an issue's assumption, and never
opens the target entry's body. That is what keeps its cost flat however many
issues there are. A hundred issues across six slices becomes six repair
batches, and the expensive reading happens inside a session that was going
to load that column anyway.

It does two things: group by target slice, and work out which issues cannot
be handled as ordinary repairs. Everything else is left to the repair itself.

Two conditions take an issue out of the queue and give it to a human instead
of repairing it, and both are computable from the header plus the schedule:

- **Round past the cap.** A repair may raise its own issues, which may raise
  their own. Without a cap the system oscillates and nobody notices until it
  has burned a day.
- **A semantic issue reaching backwards into a completed wave.** Not a
  cascade -- a conflict. Everything derived from that entry in the waves
  since is now suspect, and that signal means the slicing itself was wrong,
  which is not something to repair automatically. An *additive* issue
  reaching backwards is fine: it invalidates nothing, so nothing behind it
  moves.

Nothing here dispatches. mu-spec does not spawn processes. This produces the
batches as data; whatever runs a repair session reads them and lives
elsewhere.
"""

from __future__ import annotations

import dataclasses

from mu_spec.graph import Graph
from mu_spec.identifiers import InvalidIdentifier, parse, sort_key
from mu_spec.issues import MAX_ROUNDS, SEMANTIC, Issue
from mu_spec.storage import Manifest
from mu_spec.waves import schedule

ROUND_CAP = "round_cap"
REACHES_BACK = "reaches_back"


@dataclasses.dataclass(frozen=True)
class Escalation:
    issue: str
    reason: str
    detail: str


@dataclasses.dataclass(frozen=True)
class Batch:
    """One repair session's worth of work: every open issue against one
    slice, handled together."""

    slice: str
    wave: int | None
    issues: tuple[Issue, ...]
    # Entries that consumed a meaning one of these issues says has moved.
    # Computed from the entries' own edges, at entry level -- if B*31 changed,
    # only what declares depends_on B*31 is invalid, which is usually a
    # handful rather than a column.
    rerun: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "slice": self.slice,
            "wave": self.wave,
            "issues": [i.header() for i in self.issues],
            "rerun": list(self.rerun),
        }


def rerun_scope(graph: Graph, targets: list[str]) -> tuple[str, ...]:
    """Which entries consumed the old meaning of these ones.

    Entry level, never slice level. A semantic change to one entry
    invalidates what declared a dependency on *that entry*, not everything
    that happens to sit in the same file. Only direct dependents: an entry
    two hops away consumed its own neighbour's meaning, and whether that
    moved is not known until the neighbour is actually repaired.
    """
    out: set[str] = set()
    for raw in targets:
        try:
            identifier = parse(raw)
        except InvalidIdentifier:
            # An issue against something unparseable is still a real request
            # for a human; it just has no computable scope.
            continue
        out.update(str(i) for i in graph.dependents(identifier))
    return tuple(sorted(out, key=lambda s: sort_key(parse(s))))


def route(
    manifest: Manifest, graph: Graph, issues: list[Issue]
) -> tuple[list[Batch], list[Escalation]]:
    """Group open issues into one batch per target slice.

    Batches come back in **dependency order** -- by wave, then by name.
    Ordering within a wave is arbitrary but deterministic, which is what
    reproducibility needs; there are no edges between same-wave slices to
    order by anyway.
    """
    waves = schedule(manifest, graph).wave_of()

    escalations: list[Escalation] = []
    routable: list[Issue] = []
    for issue in issues:
        if issue.round > MAX_ROUNDS:
            escalations.append(
                Escalation(
                    issue.id,
                    ROUND_CAP,
                    f"raised in round {issue.round}, past the cap of "
                    f"{MAX_ROUNDS}. Repairs that keep raising repairs are "
                    "oscillating, not converging",
                )
            )
            continue

        here = waves.get(issue.raised_by) if issue.raised_by else None
        there = waves.get(issue.target_slice) if issue.target_slice else None
        if (
            issue.kind == SEMANTIC
            and here is not None
            and there is not None
            and there < here
        ):
            escalations.append(
                Escalation(
                    issue.id,
                    REACHES_BACK,
                    f"{issue.raised_by!r} is in wave {here} and says an entry "
                    f"in {issue.target_slice!r} (wave {there}) means something "
                    "else. That wave is already complete, so everything "
                    "derived from it since is suspect -- this is the slicing "
                    "being wrong, not a repair",
                )
            )
            continue

        routable.append(issue)

    grouped: dict[str | None, list[Issue]] = {}
    for issue in routable:
        grouped.setdefault(issue.target_slice, []).append(issue)

    batches = [
        Batch(
            slice=name or "",
            wave=waves.get(name) if name else None,
            issues=tuple(members),
            rerun=rerun_scope(
                graph, [i.target for i in members if i.kind == SEMANTIC]
            ),
        )
        for name, members in grouped.items()
    ]
    # Unscheduled slices last: they are in a cycle the gates already refuse,
    # and there is no wave to order them by.
    batches.sort(key=lambda b: (b.wave is None, b.wave or 0, b.slice))
    return batches, escalations
