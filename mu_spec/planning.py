"""Spec to code: what changed, who must change with it, and what they may read.

The planner's input is a **spec-level diff** -- which spec entries were added
or superseded -- never a git diff. Feeding it a git diff makes code the
source of truth: the planner starts reasoning about what the code does rather
than what the spec says it should do, and within a few cycles the spec layer
is decorative.

The diff falls out of propagation for free. Identifiers are allocated in
creation order from a per-layer counter that only ever moves up, so "the spec
entries created since state N" is just "the spec entries numbered above N".
No history file, no timestamps, no second copy of anything that could drift.

It resolves into two sets with different permissions:

- **write set** -- modules declaring they implement a changed entry. Editable.
- **read set** -- modules implementing entries that *depend on* a changed one
  but did not themselves change. Read-only context.

That read set is the point of the whole exercise: it turns "peek at related
features" from the executor wandering the repo into a bounded, computed
operation. And it is computed from entry-level edges, so it is the modules
that actually consumed the changed meaning -- not everything in the slice.

Git diff belongs here too, but **afterwards, as audit**. This module never
runs git and never reads a repository: the caller passes the paths it
touched, and this compares them against what was declared. mu-spec does not
execute.
"""

from __future__ import annotations

import dataclasses

from mu_spec.graph import Graph
from mu_spec.identifiers import Identifier, sort_key
from mu_spec.storage import Manifest

SPEC = "S"


@dataclasses.dataclass(frozen=True)
class SpecDiff:
    # Entries created since the mark that replace an earlier one. The
    # interesting half: something that already had modules behind it now
    # means something else.
    superseding: tuple[Identifier, ...] = ()
    # What those retired. These are what the modules in the write set were
    # written against.
    retired: tuple[Identifier, ...] = ()
    # Created since the mark and replacing nothing. New work.
    added: tuple[Identifier, ...] = ()

    @property
    def changed(self) -> tuple[Identifier, ...]:
        return tuple(sorted(self.superseding + self.added, key=sort_key))


def spec_diff(graph: Graph, since: int) -> SpecDiff:
    """Which spec entries have appeared since the spec counter stood at
    `since`.

    `since=0` is the whole spec layer, which is the correct answer for a
    first iteration: everything is new.
    """
    superseding, added, retired = [], [], []
    for entry in graph.entries():
        if entry.id.layer != SPEC or entry.id.number <= since:
            continue
        if entry.supersedes is None:
            added.append(entry.id)
        else:
            superseding.append(entry.id)
            retired.append(entry.supersedes)
    return SpecDiff(
        superseding=tuple(sorted(superseding, key=sort_key)),
        retired=tuple(sorted(retired, key=sort_key)),
        added=tuple(sorted(added, key=sort_key)),
    )


def plan(manifest: Manifest, graph: Graph, diff: SpecDiff) -> dict:
    """Resolve a spec diff into the sets a planner acts on.

    The write set is keyed by module rather than by entry, because a module
    implementing two changed entries is one task, not two -- emitting forty
    near-identical tickets means the change was misclassified.
    """
    changed = set(diff.changed)

    # A superseded entry's modules were written against the old meaning, so
    # they are in the write set even though the retired identifier is not
    # itself "changed" -- it is the reason they have to change.
    write: dict[str, set[Identifier]] = {}
    for identifier in list(changed) + list(diff.retired):
        for path in manifest.implementers(identifier):
            write.setdefault(path, set()).add(identifier)

    # Entries that consumed a changed meaning but did not change themselves.
    # Their modules are context, never editable.
    consumers: set[Identifier] = set()
    for identifier in changed | set(diff.retired):
        consumers.update(
            i for i in graph.dependents(identifier) if i not in changed
        )

    read: dict[str, set[Identifier]] = {}
    for identifier in consumers:
        for path in manifest.implementers(identifier):
            if path not in write:
                read.setdefault(path, set()).add(identifier)

    # Added entries nothing implements yet. Not a failure -- it is the new
    # work -- but it has to be visible, or a planner silently produces no
    # task for a requirement that has no file yet.
    unimplemented = [
        str(i) for i in diff.added if not manifest.implementers(i)
    ]

    def rows(mapping):
        return [
            {
                "path": path,
                "implements": sorted((str(i) for i in ids)),
                "slice": manifest.slice_of(sorted(ids, key=sort_key)[0]),
            }
            for path, ids in sorted(mapping.items())
        ]

    return {
        "diff": {
            "added": [str(i) for i in diff.added],
            "superseding": [str(i) for i in diff.superseding],
            "retired": [str(i) for i in diff.retired],
        },
        "write_set": rows(write),
        "read_set": rows(read),
        "unimplemented": sorted(unimplemented),
        "audit": {
            "editable_paths": sorted(write),
            "rule": "any file touched outside editable_paths is a gate "
            "failure -- either the planner missed a dependency or the "
            "executor freelanced. Both are worth knowing",
        },
    }


def audit(touched: list[str], editable: list[str]) -> dict:
    """Compare what was actually changed against what was declared.

    This is where a git diff belongs -- afterwards, and as evidence rather
    than as input. The caller runs git and passes the paths; this unit never
    executes anything.

    Two findings, and they mean different things. A file touched outside the
    write set is either a dependency the planner missed or an executor going
    off-piste. A declared file left untouched is weaker but still worth
    seeing: usually the change was smaller than the spec implied.
    """
    touched_set = {p for p in touched if isinstance(p, str) and p.strip()}
    editable_set = set(editable)
    undeclared = sorted(touched_set - editable_set)
    return {
        "clean": not undeclared,
        "undeclared": undeclared,
        "declared_untouched": sorted(editable_set - touched_set),
        "detail": (
            "every touched file was declared"
            if not undeclared
            else "files were touched that no changed spec entry accounts for: "
            "either the planner missed a dependency or the executor freelanced"
        ),
    }
