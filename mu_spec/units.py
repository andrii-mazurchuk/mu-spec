"""Work units: what a single piece of work is, and what it may write.

A **work unit** is a maximal connected subgraph of the entry-to-module graph
-- spec entries and the files that implement them, joined transitively by
implements-edges, with nothing outside connecting in. One unit is one branch.

The grain matters and was settled by measurement rather than argument. An
**entry** is too fine: two entries living in one file would both write it, and
`bot.py` holds eight. A **module** is too fine in the other direction: an entry
spans files -- `dark`'s `S*73` spans `.env`, `pyproject.toml`, a justfile, a
compose file and a preconditions module, and eleven of its sixty-eight entries
span more than one. A **slice** is too coarse: it over-serialises, and a file
that straddles two slices belongs to neither exclusively.

Maximal-connected is not a preference among those. It is the *smallest*
grouping whose write set is disjoint from every other unit's, and that
disjointness is the whole point: two branches in the same wave cannot produce
a merge conflict, because no file is in both. The merging is forced by "no two
units touch the same file", not chosen.

**Disjointness is not independence.** A unit's entries may still `depends_on`
another unit's, and then it waits. Running two things at once needs both
disjoint files (given here by construction) and no edge between them (given by
the projection below).

Nothing here is authored. There is no field to declare a unit in and no field
to declare an edge in, for the same reason slice dependency has none: two
statements of one fact drift, and the maintained one goes stale.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable

from mu_spec.graph import Graph
from mu_spec.identifiers import Identifier, parse, sort_key
from mu_spec.slice_gates import cycles
from mu_spec.storage import Manifest
from mu_spec.waves import Schedule, schedule_edges

SPEC = "S"
# Twelve hex of a sha256 over the sorted entry list. Long enough that a
# collision across a few hundred units is not a thing worth a line of code,
# short enough to read in a URL and in a log.
KEY_LENGTH = 12


def unit_key(entries: Iterable[Identifier]) -> str:
    """The content address of a work unit: a digest of the entries it holds.

    Identity **is** the entry set. Same entries, same unit; different
    entries, different unit. Nothing is allocated, nothing is stored, and
    two projections of the same graph agree without having to consult each
    other -- which is what lets a cut be compared with a later one at all.
    """
    canonical = "\n".join(str(i) for i in sorted(entries, key=sort_key))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:KEY_LENGTH]


@dataclasses.dataclass(frozen=True)
class Unit:
    key: str
    entries: tuple[Identifier, ...]
    # The write set, and the only thing a branch for this unit may edit.
    modules: tuple[str, ...]
    # Which slices this unit's entries belong to. More than one means a file
    # in it straddles a slice boundary -- reported, never refused.
    slices: tuple[str, ...] = ()
    # True when this unit is the contraction of two or more components that
    # depended on each other. Their files cannot be built separately, which
    # is a fact about the module map worth seeing.
    cycle: bool = False

    @property
    def cross_slice(self) -> bool:
        return len(self.slices) > 1

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "entries": [str(i) for i in self.entries],
            "modules": list(self.modules),
            "slices": list(self.slices),
            "cycle": self.cycle,
        }


@dataclasses.dataclass(frozen=True)
class Projection:
    units: tuple[Unit, ...]
    # Unit key -> the keys it must follow. THIS is the contract a consumer
    # honours. Waves are a view computed from it, and a consumer that waits
    # for a whole wave instead of its own blockers waits for work it does
    # not need: on `dark` the last unit would wait for fifty-one others when
    # it actually waits for two.
    edges: dict[str, tuple[str, ...]]
    schedule: Schedule
    # Live spec entries no live module claims. They are in no unit, so they
    # are in no piece of work -- which is exactly why this is reported
    # loudly rather than left to be noticed.
    unimplemented: tuple[Identifier, ...] = ()
    # Modules claiming only entries that have since been superseded. The
    # manifest is validated when a module is declared and never again, so a
    # module can go on naming a retired identifier; one naming *only* retired
    # identifiers drops out of every write set silently.
    stale_modules: tuple[str, ...] = ()
    # depends_on targets that belong to no unit. The edge is real and cannot
    # be drawn, so the order it implies is invisible.
    dangling: tuple[Identifier, ...] = ()

    def by_key(self) -> dict[str, Unit]:
        return {u.key: u for u in self.units}

    def unit_of(self) -> dict[Identifier, str]:
        return {e: u.key for u in self.units for e in u.entries}

    def to_json(self) -> dict:
        return {
            "units": [u.to_json() for u in self.units],
            "edges": {k: list(v) for k, v in sorted(self.edges.items())},
            "waves": [
                {"wave": n, "units": list(w), "width": len(w)}
                for n, w in enumerate(self.schedule.waves)
            ],
            "unschedulable": list(self.schedule.unschedulable),
            "unimplemented": [str(i) for i in self.unimplemented],
            "stale_modules": list(self.stale_modules),
            "dangling": [str(i) for i in self.dangling],
        }


class _Find:
    """Union-find over module paths.

    Deliberately over paths alone rather than over a mixed entry/module node
    set. A component that holds a module is exactly a group of paths closed
    under "shares an entry", and the entries supply the relation -- so there
    is no second kind of node, no tagging scheme, and no question about
    whether a path could collide with a stringified identifier.
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:      # path compression, iterative
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)   # deterministic root

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self._parent:
            out.setdefault(self.find(item), []).append(item)
        return out


def _invert(
    manifest: Manifest, graph: Graph
) -> tuple[dict[Identifier, list[str]], dict[Identifier, str], list[str]]:
    """One pass each, rather than a linear scan per lookup.

    `Manifest.implementers` and `Manifest.slice_of` both scan; calling either
    once per entry per module is the quadratic nobody notices until a project
    is large. Neither is called from this module.

    Dead identifiers are dropped here, which is also where they are counted:
    a module is checked against the live graph when it is declared and never
    again, so it can outlive what it claims.
    """
    entry_modules: dict[Identifier, list[str]] = {}
    stale: list[str] = []
    for path, claimed in manifest.modules.items():
        live = [i for i in claimed if i in graph]
        if claimed and not live:
            stale.append(path)
            continue
        if len(live) != len(claimed):
            stale.append(path)
        for identifier in live:
            entry_modules.setdefault(identifier, []).append(path)

    entry_slice: dict[Identifier, str] = {}
    for name, sl in manifest.slices.items():
        for identifier in sl.members:
            entry_slice[identifier] = name

    for paths in entry_modules.values():
        paths.sort()
    return entry_modules, entry_slice, sorted(set(stale))


def _components(entry_modules: dict[Identifier, list[str]]) -> list[list[str]]:
    """Module paths grouped into maximal connected sets."""
    find = _Find()
    for paths in entry_modules.values():
        for path in paths:
            find.add(path)
        first = paths[0]
        for other in paths[1:]:
            find.union(first, other)
    return [sorted(members) for members in find.groups().values()]


def _build(
    components: list[list[str]],
    entry_modules: dict[Identifier, list[str]],
    entry_slice: dict[Identifier, str],
) -> list[Unit]:
    owner: dict[str, int] = {}
    for index, members in enumerate(components):
        for path in members:
            owner[path] = index

    held: list[list[Identifier]] = [[] for _ in components]
    for identifier, paths in entry_modules.items():
        held[owner[paths[0]]].append(identifier)

    units = []
    for index, members in enumerate(components):
        entries = tuple(sorted(held[index], key=sort_key))
        slices = tuple(sorted({entry_slice[e] for e in entries if e in entry_slice}))
        units.append(
            Unit(
                key=unit_key(entries),
                entries=entries,
                modules=tuple(members),
                slices=slices,
            )
        )
    return units


def _edges(units: list[Unit], graph: Graph) -> tuple[dict[str, tuple[str, ...]], list[Identifier]]:
    """Unit A follows unit B when an entry in A depends on an entry in B.

    `emits_into` contributes nothing, here as everywhere: an emission is
    fire-and-forget, nothing is consumed back, so it imposes no order. Only
    `depends_on` is an order.

    Every unit is seeded as a key even when it follows nothing, because the
    relaxation assigns only nodes it can see and would otherwise strand
    everything downstream of a node that appears solely as a target.
    """
    of = {e: u.key for u in units for e in u.entries}
    out: dict[str, set[str]] = {u.key: set() for u in units}
    dangling: set[Identifier] = set()
    for unit in units:
        for entry in unit.entries:
            for target in graph.dependencies(entry):
                other = of.get(target)
                if other is None:
                    dangling.add(target)
                elif other != unit.key:        # a dependency resolved inside
                    out[unit.key].add(other)   # one unit imposes no order
    return (
        {k: tuple(sorted(v)) for k, v in out.items()},
        sorted(dangling, key=sort_key),
    )


def _contract(
    units: list[Unit], edges: dict[str, tuple[str, ...]], graph: Graph
) -> tuple[list[Unit], dict[str, tuple[str, ...]], list[Identifier]]:
    """Merge units that depend on each other into one.

    Grouping entries into files can create a cycle the entry graph does not
    have -- two files each implementing one end of the other's dependency --
    and `depends_on` is acyclic at entry level because the gates require it.
    Merging is the correct answer rather than a fudge: files that depend on
    each other cannot be built separately, so they are one piece of work.

    One pass reaches the fixed point. Condensing every strongly connected
    component at once yields the condensation, which is acyclic by
    construction, so a merge cannot create a new cycle. If that were ever
    wrong it would surface as a non-empty `schedule.unschedulable` rather
    than as a hang, which is why there is no iteration cap here and no
    assertion: the check already exists and is already reported.

    A one-node cycle needs no handling -- a dependency resolved inside a
    single unit is dropped as a self-edge before this runs.
    """
    groups = [g for g in cycles(edges) if len(g) > 1]
    if not groups:
        return units, edges, []

    by_key = {u.key: u for u in units}
    merged_of: dict[str, str] = {}
    survivors: list[Unit] = []
    grouped: set[str] = set()

    for members in groups:
        parts = [by_key[k] for k in members]
        entries = tuple(sorted({e for p in parts for e in p.entries}, key=sort_key))
        modules = tuple(sorted({m for p in parts for m in p.modules}))
        slices = tuple(sorted({s for p in parts for s in p.slices}))
        unit = Unit(
            key=unit_key(entries),
            entries=entries,
            modules=modules,
            slices=slices,
            cycle=True,
        )
        survivors.append(unit)
        for key in members:
            merged_of[key] = unit.key
            grouped.add(key)

    survivors.extend(u for u in units if u.key not in grouped)
    survivors.sort(key=lambda u: sort_key(u.entries[0]) if u.entries else ("", 0))
    rebuilt, dangling = _edges(survivors, graph)
    return survivors, rebuilt, dangling


def project(manifest: Manifest, graph: Graph) -> Projection:
    """Compute the work units of a project.

    Deterministic and side-effect free. An empty module map yields no units
    and every spec entry unimplemented -- described rather than refused,
    because a person who cannot cut needs to be able to see why.
    """
    entry_modules, entry_slice, stale = _invert(manifest, graph)
    units = _build(_components(entry_modules), entry_modules, entry_slice)
    units.sort(key=lambda u: sort_key(u.entries[0]) if u.entries else ("", 0))

    edges, dangling = _edges(units, graph)
    units, edges, after = _contract(units, edges, graph)
    # Union rather than substitution. Contraction cannot change which targets
    # are dangling -- merging units only ever gives an edge somewhere to land
    # -- so the two sets agree today. Replacing one with the other would be
    # relying on that; combining them does not have to.
    dangling = sorted(set(dangling) | set(after), key=sort_key)

    unimplemented = tuple(
        sorted(
            (e.id for e in graph.entries()
             if e.id.layer == SPEC and e.id not in entry_modules),
            key=sort_key,
        )
    )
    return Projection(
        units=tuple(units),
        edges=edges,
        schedule=schedule_edges(edges),
        unimplemented=unimplemented,
        stale_modules=tuple(stale),
        dangling=tuple(dangling),
    )


# =====================================================================
# THE CUT
#
# A projection is computed. A **cut** is a projection a person decided to
# take, and the difference is the whole reason this is stored at all: the
# gates going green is not the same event as the author being finished. A
# corpus can be sound and still be mid-revision, and a projection that
# recomputed itself silently would move work units under someone who was
# still writing -- or worse, after work had already been handed out from
# them.
#
# So: computed, materialised on a human trigger, never automatically.
#
# What the file holds is this unit's own projection of its own graph.
# Nothing about who consumed it, no foreign identifier, nothing that could
# point outside. Re-cutting is free and expected.
# =====================================================================


@dataclasses.dataclass(frozen=True)
class Cut:
    seq: int
    at: float
    note: str
    units: tuple[Unit, ...]
    edges: dict[str, tuple[str, ...]]
    unimplemented: tuple[Identifier, ...] = ()
    stale_modules: tuple[str, ...] = ()

    def by_key(self) -> dict[str, Unit]:
        return {u.key: u for u in self.units}

    def to_json(self) -> dict:
        schedule = schedule_edges(self.edges)
        return {
            "seq": self.seq,
            "at": self.at,
            "note": self.note,
            "units": [u.to_json() for u in self.units],
            "edges": {k: list(v) for k, v in sorted(self.edges.items())},
            "waves": [
                {"wave": n, "units": list(w), "width": len(w)}
                for n, w in enumerate(schedule.waves)
            ],
            "unschedulable": list(schedule.unschedulable),
            "unimplemented": [str(i) for i in self.unimplemented],
            "stale_modules": list(self.stale_modules),
        }


class CutError(ValueError):
    """A cut log that cannot be read back.

    A hard error, unlike a missing proposal. A proposal that will not parse
    degrades to "no proposal pending", which is true and harmless. A cut
    that will not parse degrades to an incomplete write set, and a write set
    is the only thing standing between two branches and a merge conflict.
    """


def _unit_body(unit: Unit) -> dict:
    return {
        "entries": [str(i) for i in unit.entries],
        "modules": list(unit.modules),
        "slices": list(unit.slices),
        "cycle": unit.cycle,
    }


def _unit_from_body(key: str, body: dict) -> Unit:
    return Unit(
        key=key,
        entries=tuple(parse(i) for i in body.get("entries", ())),
        modules=tuple(body.get("modules", ())),
        slices=tuple(body.get("slices", ())),
        cycle=bool(body.get("cycle", False)),
    )


def read_cuts(path: Path) -> tuple[Cut, ...]:
    """Every cut, oldest first, with carried-forward units resolved.

    One forward pass with a rolling table of bodies. A cut lists every key
    it holds in `members`, but carries a *body* only for the units that
    actually changed -- so an unchanged unit is written once and referenced
    thereafter, which is what keeps a log of frequent re-cuts small.
    """
    if not path.exists():
        return ()
    bodies: dict[str, dict] = {}
    out: list[Cut] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError as exc:
            raise CutError(f"{path}:{number} is not valid JSON: {exc}") from exc
        bodies.update(raw.get("units") or {})
        members = raw.get("members") or []
        missing = [k for k in members if k not in bodies]
        if missing:
            raise CutError(
                f"{path}:{number} names work {'units' if len(missing) > 1 else 'unit'} "
                f"{', '.join(missing)} whose contents appear in no earlier cut. The "
                "log is truncated or was written by something else, and a cut missing "
                "a unit hands out an incomplete write set"
            )
        out.append(
            Cut(
                seq=raw.get("seq", number),
                at=raw.get("at", 0.0),
                note=raw.get("note", ""),
                units=tuple(_unit_from_body(k, bodies[k]) for k in members),
                edges={
                    k: tuple(v) for k, v in (raw.get("edges") or {}).items()
                },
                unimplemented=tuple(parse(i) for i in raw.get("unimplemented", ())),
                stale_modules=tuple(raw.get("stale_modules", ())),
            )
        )
    return tuple(out)


def current_cut(path: Path) -> Cut | None:
    cuts = read_cuts(path)
    return cuts[-1] if cuts else None


def append_cut(
    path: Path,
    projection: Projection,
    now_fn: Callable[[], float],
    note: str = "",
) -> Cut:
    """Record a projection as a cut.

    A unit's body is written when its key is new **or when its contents
    moved**, and the second half matters more than it looks. Identity is the
    entry set, so declaring a module that implements entries already inside
    a unit leaves the key untouched while changing the write set. Deciding
    "unchanged" by key alone would store nothing and go on handing out the
    old set of files -- which is precisely the guarantee that must not rot.
    """
    previous = read_cuts(path)
    latest: dict[str, dict] = {}
    for cut in previous:
        for unit in cut.units:
            latest[unit.key] = _unit_body(unit)

    changed = {
        u.key: _unit_body(u)
        for u in projection.units
        if latest.get(u.key) != _unit_body(u)
    }
    record = {
        "seq": (previous[-1].seq + 1) if previous else 1,
        "at": now_fn(),
        "note": note,
        "members": [u.key for u in projection.units],
        "units": changed,
        "edges": {k: list(v) for k, v in sorted(projection.edges.items())},
        "unimplemented": [str(i) for i in projection.unimplemented],
        "stale_modules": list(projection.stale_modules),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return Cut(
        seq=record["seq"],
        at=record["at"],
        note=note,
        units=projection.units,
        edges=projection.edges,
        unimplemented=projection.unimplemented,
        stale_modules=projection.stale_modules,
    )


def drift(cut: Cut, live: Projection) -> dict:
    """How the live projection differs from the cut work was handed out from.

    Computed, never stored: a recorded drift is a lie the moment the next
    amendment lands, and this design's whole position on projected facts is
    that the maintained copy is the one that goes stale.

    One shape, not a taxonomy. Split, merge, grown, shrunk, appeared and
    vanished are all the same thing seen from different sides -- the shape
    of one connected group in the "shares an entry" relation between the
    stored units and the live ones. A vocabulary would have to pick a name
    for a three-into-two regrouping and would be lying when it did.

        was [k]      now [k1, k2]   a split
        was [k1, k2] now [k]        a merge
        was [k]      now []         gone
        was []       now [k]        new

    So "is this the same piece of work" has a direct answer: the key is in
    `unchanged`, or it appears in exactly one group that names what replaced
    it.
    """
    was = cut.by_key()
    now = live.by_key()

    find = _Find()
    for key in was:
        find.add("was:" + key)
    for key in now:
        find.add("now:" + key)
    holder: dict[Identifier, list[str]] = {}
    for key, unit in was.items():
        for entry in unit.entries:
            holder.setdefault(entry, []).append("was:" + key)
    for key, unit in now.items():
        for entry in unit.entries:
            holder.setdefault(entry, []).append("now:" + key)
    for sides in holder.values():
        for other in sides[1:]:
            find.union(sides[0], other)

    unchanged: list[str] = []
    groups: list[dict] = []
    for members in find.groups().values():
        before = sorted(m[4:] for m in members if m.startswith("was:"))
        after = sorted(m[4:] for m in members if m.startswith("now:"))
        if (
            len(before) == 1
            and after == before
            and _unit_body(was[before[0]]) == _unit_body(now[after[0]])
        ):
            unchanged.append(before[0])
            continue
        groups.append({"was": before, "now": after})

    groups.sort(key=lambda g: (g["was"], g["now"]))
    return {
        "unchanged": sorted(unchanged),
        "changed": groups,
        "edges_changed": {k: tuple(v) for k, v in cut.edges.items()} != live.edges,
    }
