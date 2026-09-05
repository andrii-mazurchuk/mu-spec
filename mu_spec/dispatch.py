"""Which session runs next, decided from the graph alone.

The ladder below is the whole module. Everything it asks is already
answerable -- unserved entries, slice membership, wave order, open batches --
so selection is arithmetic over state that exists, never a judgement about
what is worth doing. What comes back names a session type and the scope it
is allowed to see; it does not start anything.

The order is the design. Two rules produced it:

- **In flight beats new.** A correction that has not been validated upward,
  and a wave's own fallout, are finished before fresh input is let in.
  Otherwise a wave's failures trail into the next one, which is exactly the
  blast radius the graph exists to bound.
- **Structure beats content.** Slicing outranks derivation because deriving
  into a partition that is about to move means cutting it around work
  already done.

The floor matters as much as the rungs: when nothing is eligible this
returns `None` and the caller runs nothing. A loop with no floor launches a
session whose only finding is that there was no work.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable, Sequence

from .gates import UNSERVED, unserved
from .graph import Graph
from .identifiers import LAYERS, Identifier
from .storage import Manifest
from .waves import schedule

TRIAGE = "triage"
BACK_CHECK = "back_check"
SLICING = "slicing"
DERIVATION = "derivation"
REPAIR = "repair"
BUILD = "build"

# Directory names under session_types/, and the order the ladder tries them.
SESSION_TYPES = (BACK_CHECK, REPAIR, TRIAGE, SLICING, DERIVATION, BUILD)


@dataclasses.dataclass(frozen=True)
class Dispatch:
    """One session's worth of work: what to run, and everything it may see.

    `scope` is the guardrail. It is computed before the session starts, so a
    session is bounded by what it was handed rather than by being told to
    restrain itself -- the same reason a work package exists.
    """

    session_type: str
    project: str
    scope: dict[str, Any]
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "session_type": self.session_type,
            "project": self.project,
            "scope": self.scope,
            "reason": self.reason,
        }

    def brief(self, base_url: str) -> str:
        """The dynamic half of the session's prompt -- everything the static
        `CLAUDE.md` in its directory cannot know.

        Deliberately short. It names the scope and where to reach the unit;
        it does not pre-load the spine or the bodies, because the session has
        HTTP access and its own prompt already says what to fetch. Copying
        graph content in here would make the brief a second, staler copy of
        the graph.
        """
        lines = [
            "# Session brief",
            "",
            f"You are a `{self.session_type}` session. Your contract is the",
            "`CLAUDE.md` already loaded from this directory, and the shared contract",
            "it imports. Both are already in your context; you do not need to fetch",
            "them.",
            "",
            f"- mu-spec base URL: {base_url}",
            f"- project: {self.project or '(none yet -- this request creates it)'}",
            f"- why you were selected: {self.reason}",
            "",
            "## Your scope",
            "",
            "This is the complete list for this session. The wave order, the layer",
            "boundary and the ownership rules were applied before you started --",
            "do not go looking for more work.",
            "",
        ]
        for key, value in self.scope.items():
            rendered = ", ".join(str(v) for v in value) if isinstance(value, list) else value
            lines.append(f"- {key}: {rendered}")

        # Without this the brief only *describes* the situation, and the first
        # live session read it as context and replied "I'm ready. What do you
        # need?" -- there was nobody to answer, so it exited having done
        # nothing, and a clean exit was recorded as success.
        lines += [
            "",
            "## Begin",
            "",
            "Do this work now, using the HTTP API above.",
            "",
            "**This is a headless run.** There is nobody to answer a question, and no",
            "confirmation is coming -- asking one ends the session having done nothing.",
            "If something is genuinely undecidable, the contract tells you what to do:",
            "file an issue, state the assumption, and carry on.",
            "",
            "Finish the work in your scope, then stop.",
        ]
        return "\n".join(lines) + "\n"


def _sliced(manifest: Manifest) -> set[Identifier]:
    return {member for s in manifest.slices.values() for member in s.members}


def _slice_of(manifest: Manifest, identifier: Identifier) -> str | None:
    for name, sl in manifest.slices.items():
        if identifier in sl.members:
            return name
    return None


def select(
    manifest: Manifest,
    graph: Graph,
    *,
    pending: Sequence[str] = (),
    unchecked: Sequence[str] = (),
    batches: Sequence[Any] = (),
    can_build: bool = True,
) -> Dispatch | None:
    """The next session to run, or `None` when nothing is eligible.

    `pending` and `unchecked` are inbox message identifiers -- the session
    fetches the text itself rather than having it copied in here. `batches`
    are `reconcile.Batch` records, already in dependency order.
    """
    project = manifest.project

    def dispatch(kind: str, scope: dict[str, Any], reason: str) -> Dispatch:
        return Dispatch(kind, project, scope, reason)

    # 1. A correction placed but not yet checked against the layer above.
    #    Nothing may be built on top of it until that question is answered.
    if unchecked:
        return dispatch(
            BACK_CHECK,
            {"request": unchecked[0]},
            "a correction has not been checked against the layer above",
        )

    # 2. The wave that just ended left issues open.
    if batches:
        batch = batches[0]
        return dispatch(
            REPAIR,
            {
                "slice": batch.slice,
                "wave": batch.wave,
                "issues": [i.header() for i in batch.issues],
                "rerun": list(batch.rerun),
            },
            f"{len(batch.issues)} open issue(s) against {batch.slice}",
        )

    # 3. New input at the door.
    if pending:
        return dispatch(
            TRIAGE,
            {"request": pending[0]},
            "an unresolved request is waiting in the inbox",
        )

    # 4. Behaviour exists that no slice owns. Nothing below it can be
    #    derived, because there would be no slice to own the result.
    owned = _sliced(manifest)
    unsliced = sorted(
        (e.id for e in graph.entries() if e.id.layer == "B" and e.id not in owned),
        key=lambda i: (i.depth, i.number),
    )
    if unsliced:
        return dispatch(
            SLICING,
            {"unsliced": [str(i) for i in unsliced]},
            f"{len(unsliced)} behaviour entr(ies) belong to no slice",
        )

    # 5. Something above has nothing serving it. Derive the layer below it,
    #    earliest wave first so a slice never reads a dependency that is
    #    still being written.
    pick = _next_derivation(manifest, graph)
    if pick is not None:
        return pick

    # 6. Spec that no module implements.
    #
    # Skipped entirely when there is nowhere to write. A build session runs
    # with cwd inside this unit's own repository, so dispatching one with no
    # target configured means writing the target project's code into
    # mu-spec. Refusing to dispatch is the only safe default.
    if not can_build:
        return None
    implemented = {i for ids in manifest.modules.values() for i in ids}
    for name in _slice_order(manifest, graph):
        spec = {i for i in manifest.slices[name].members if i.layer == "S"}
        if spec and spec - implemented:
            return dispatch(
                BUILD,
                {"slice": name, "spec": sorted(str(i) for i in spec)},
                f"{len(spec - implemented)} spec entr(ies) in {name} "
                "are implemented by no module",
            )

    return None


def _slice_order(manifest: Manifest, graph: Graph) -> list[str]:
    """Slice names in the order they may be worked. A slice caught in a cycle
    has no wave; it sorts last rather than raising, because an unschedulable
    graph is already refused by the gates and selection is not the place to
    discover it.
    """
    wave_of = schedule(manifest, graph).wave_of()
    unscheduled = len(manifest.slices)
    return sorted(manifest.slices, key=lambda n: (wave_of.get(n, unscheduled), n))


def _next_derivation(manifest: Manifest, graph: Graph) -> Dispatch | None:
    findings = [f for f in unserved(graph) if f.kind == UNSERVED]
    if not findings:
        return None

    sched = schedule(manifest, graph)
    wave_of = sched.wave_of()

    def rank(identifier: Identifier) -> tuple[int, int, int]:
        name = _slice_of(manifest, identifier)
        # Unsliced parents are intent, which is derived before any slice
        # exists -- depth already sorts it above everything sliced.
        return (identifier.depth, wave_of.get(name, 0) if name else 0, identifier.number)

    parents = sorted((f.id for f in findings), key=rank)
    head = parents[0]
    name = _slice_of(manifest, head)
    layer = LAYERS[head.depth + 1]

    # Everything unserved at the same layer, in the same slice, is one
    # session's work: it writes one layer of one slice and no more.
    group = [
        p
        for p in parents
        if p.depth == head.depth and _slice_of(manifest, p) == name
    ]
    return Dispatch(
        DERIVATION,
        manifest.project,
        {
            "layer": layer,
            "slice": name,
            "parents": [str(p) for p in group],
        },
        f"{len(group)} {LAYERS[head.depth]}-layer entr(ies) have nothing serving them",
    )
