"""The derivation graph: entries, edges, traversal, and the spine.

Every entry carries an identifier, the identifiers it derives from, and a
body. Those two structural fields are the whole system -- they turn a pile of
markdown into a directed graph, and every operation worth having is a
traversal of it.

Deliberately knows nothing about layers beyond what `identifiers` already
defines, nothing about slices (that is the manifest's job), and nothing about
per-layer field shapes -- those are still undesigned and constrain only what
goes *inside* a body. Keeping them out means the shapes can land later
without touching this module.

Nothing here reads or writes the filesystem. A Graph is built from entries
someone else loaded, so the whole thing is testable without a store.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable

from mu_spec.identifiers import Identifier, sort_key


class DuplicateIdentifier(ValueError):
    """Two entries claim the same identifier. A hard error: identifiers are
    never reused, and a last-one-wins merge would silently drop whatever the
    losing entry was serving."""


@dataclasses.dataclass(frozen=True)
class Entry:
    id: Identifier
    # Vertical: what this entry serves, exactly one layer up.
    derives_from: tuple[Identifier, ...] = ()
    # Horizontal: what this entry needs from its own layer. The two edge
    # kinds answer different questions -- derives_from is justification,
    # depends_on is order -- and conflating them was what made slice
    # dependency guesswork. Slice-level dependency is projected from these
    # and is never authored, so the manifest cannot drift from the truth.
    depends_on: tuple[Identifier, ...] = ()
    # Also horizontal, and deliberately NOT a dependency: what this entry
    # publishes into a cross-cutting slice. Fire-and-forget -- nothing is
    # consumed back, so it imposes no order, which is what keeps a
    # cross-cutting slice derivable before everything that emits into it.
    # It is also the escape from a cycle involving a concern: flip the
    # concern's outbound dependency into an inbound emission and it has no
    # outbound edges left.
    emits_into: tuple[Identifier, ...] = ()
    # The one-line title the spine carries. Stored explicitly rather than
    # inferred, because the spine is the thing agents read to decide what to
    # load, and a title that silently changes when someone reflows a
    # paragraph is a bad foundation for that.
    title: str = ""
    body: str = ""
    # Set on the *replacement*, naming the entry it retires. Amendments are
    # append-only: the superseded entry stays in the file and stays
    # retrievable, it just stops participating in the live graph.
    supersedes: Identifier | None = None

    @property
    def display_title(self) -> str:
        """The explicit title, falling back to the body's first line for an
        entry written before titles were explicit. An entry with neither
        yields an empty string rather than raising -- a half-written entry
        must still be listable."""
        if self.title.strip():
            return self.title.strip()
        first = self.body.strip().splitlines()[0] if self.body.strip() else ""
        return first.lstrip("#").strip()


class Graph:
    def __init__(self, entries: Iterable[Entry]) -> None:
        self._entries: dict[Identifier, Entry] = {}
        for entry in entries:
            if entry.id in self._entries:
                raise DuplicateIdentifier(f"identifier {entry.id} used twice")
            self._entries[entry.id] = entry

        # id -> the entry that superseded it. Built once here rather than
        # scanned per query; supersedes is declared on the replacement, so
        # this is the inverse index.
        self._superseded: dict[Identifier, Identifier] = {
            entry.supersedes: entry.id
            for entry in self._entries.values()
            if entry.supersedes is not None
        }

        self._live: dict[Identifier, Entry] = {
            ident: entry
            for ident, entry in self._entries.items()
            if ident not in self._superseded
        }

        # Reverse edges, live entries only, so children() is a lookup rather
        # than a scan of every entry on every call.
        self._children: dict[Identifier, list[Identifier]] = {}
        self._dependents: dict[Identifier, list[Identifier]] = {}
        for entry in self._live.values():
            for parent in entry.derives_from:
                self._children.setdefault(parent, []).append(entry.id)
            for needed in entry.depends_on:
                self._dependents.setdefault(needed, []).append(entry.id)

    # -- lookup -------------------------------------------------------------

    def entries(self) -> tuple[Entry, ...]:
        """Live entries in spine order -- by layer, then numerically."""
        return tuple(sorted(self._live.values(), key=lambda e: sort_key(e.id)))

    def get(self, identifier: Identifier) -> Entry | None:
        """Any entry, live or superseded. History stays retrievable: excluded
        from the live graph is not the same as deleted."""
        return self._entries.get(identifier)

    def superseded_by(self, identifier: Identifier) -> Identifier | None:
        return self._superseded.get(identifier)

    def __contains__(self, identifier: object) -> bool:
        return identifier in self._live

    # -- edges --------------------------------------------------------------

    def parents(self, identifier: Identifier) -> tuple[Identifier, ...]:
        entry = self._live.get(identifier)
        return entry.derives_from if entry else ()

    def children(self, identifier: Identifier) -> tuple[Identifier, ...]:
        return tuple(sorted(self._children.get(identifier, ()), key=sort_key))

    def dependencies(self, identifier: Identifier) -> tuple[Identifier, ...]:
        """What this entry needs from its own layer."""
        entry = self._live.get(identifier)
        return entry.depends_on if entry else ()

    def dependents(self, identifier: Identifier) -> tuple[Identifier, ...]:
        """Which entries need this one. The re-run scope of a change: if this
        entry's meaning moved, exactly these consumed the old meaning."""
        return tuple(sorted(self._dependents.get(identifier, ()), key=sort_key))

    def ancestors(self, identifier: Identifier) -> tuple[Identifier, ...]:
        return self._walk([identifier], self.parents)

    def descendants(self, identifier: Identifier) -> tuple[Identifier, ...]:
        return self._walk([identifier], self.children)

    def blast_radius(self, changed: Iterable[Identifier]) -> tuple[Identifier, ...]:
        """Everything downstream of a change -- the entries whose
        justification now depends on something that moved.

        Excludes the changed entries themselves: the caller already knows what
        it changed, and including them makes "this touches N entries"
        misleading."""
        changed = list(changed)
        return self._walk(changed, self.children, exclude=set(changed))

    def _walk(
        self,
        start: list[Identifier],
        step,
        exclude: set[Identifier] | None = None,
    ) -> tuple[Identifier, ...]:
        """Breadth-first closure, `seen` guarding against cycles. Layer
        direction makes a cycle impossible in a valid graph, but an invalid
        one has to stay inspectable rather than hang the process -- the gates
        can only report on a graph they can walk.

        An edge pointing at an identifier no live entry claims is skipped
        rather than yielded: traversal results are used to fetch bodies, so
        returning an identifier that resolves to nothing would push the
        problem onto every caller. Detecting the dangling edge is the
        orphan gate's job, and it reads `derives_from` directly."""
        seen: set[Identifier] = set()
        queue = list(start)
        while queue:
            current = queue.pop(0)
            for nxt in step(current):
                if nxt not in seen and nxt in self._live:
                    seen.add(nxt)
                    queue.append(nxt)
        if exclude:
            seen -= exclude
        return tuple(sorted(seen, key=sort_key))

    # -- spine --------------------------------------------------------------

    def spine(self) -> list[dict]:
        """Identifier, one-line title, and every edge -- nothing else.

        Roughly fifteen tokens an entry. This is what gets loaded
        unconditionally, with bodies pulled by identifier only once the agent
        knows from the spine which ones it actually needs. A spine carrying
        bodies would defeat the entire scheme.

        Every edge kind is here because the spine is what an agent decides
        from. If the horizontal edges only appeared inside bodies, it would
        have to load bodies to work out which bodies it needs.

        Rows are dicts rather than tuples: this is the third edge kind, and a
        positional row means every reader counts fields correctly forever."""
        return [
            {
                "id": str(e.id),
                "title": e.display_title,
                "derives_from": [str(d) for d in e.derives_from],
                "depends_on": [str(d) for d in e.depends_on],
                "emits_into": [str(d) for d in e.emits_into],
            }
            for e in self.entries()
        ]
