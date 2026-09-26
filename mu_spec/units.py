"""Work units: what a single piece of work is, and what it may write.

A **work unit** is one spec entry together with every module implementing it.
A **test unit** is the test entries derived from one spec entry, together with
every module implementing those. Units are 1:1 with spec entries, twice over:
one implementation unit always, one test unit whenever scenarios exist and
have modules declared. One unit is one branch, and one dispatchable ticket.

The grain is settled by **bounded cognitive load**. A unit is what gets handed
to an agent as a single ticket, so its size has to be predictable and
independent of the project's worst file. Three other grains were available. An
**entry-module pair** is too fine: `t-finance`'s `S*11` spans four modules, and
splitting per pair turns one contract into four tickets somebody then has to
reassemble. A **slice** is too coarse: it over-serialises, and a file
straddling two slices belongs to neither exclusively. A **maximal connected
subgraph** -- what this module used to compute -- is unbounded: grouping by
"shares a module" propagates, so one shared entry glues two files, those files
drag in their own other entries, and on `t-finance` the component that results
is eleven entries across five modules. Nobody chose that size and nobody can
tune it; it is a property of `server.py`'s fan-out, which is what a ticket's
size must not be. Anchoring on the spec entry bounds it at four modules, 1.3
on average.

**Write sets overlap, and overlap is not an order.** A file implementing
several spec entries is written by several units, and that is expected rather
than a defect to be grouped away. `overlap` reports which units must not be
dispatched *simultaneously*. It is deliberately not an edge: beyond that
constraint they need no order, and inventing one where the spec states no
dependency would be this module scheduling, which it does not do.

**Overlap is not dependence, and disjointness is not independence.** A unit's
entries may `depends_on` another unit's, and then it waits. Two units may be
worked at once only with both -- no edge between them, and no shared file.

**Cycles cannot occur.** Units are 1:1 with spec entries, `depends_on` between
spec entries is acyclic because the gates require it, and the only other edge
kind points *into* a test unit, which has no outbound edge to leave by. A
directed acyclic graph relabelled is still one, so there is nothing here to
detect and nothing to contract.

Nothing here is authored. There is no field to declare a unit in and no field
to declare an edge in, for the same reason slice dependency has none: two
statements of one fact drift, and the maintained one goes stale.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Callable

from mu_spec.graph import Graph
from mu_spec.identifiers import TEST, Identifier, parse, sort_key
from mu_spec.storage import Manifest
from mu_spec.waves import Schedule, schedule_edges

SPEC = "S"
# What marks a test unit's key apart from its implementation unit's. Both are
# anchored to the same spec entry, so the anchor alone does not name them.
TEST_SUFFIX = ":T"


def unit_key(anchor: Identifier, tests: bool = False) -> str:
    """The name of a work unit: its anchor spec entry, and which kind it is.

    Identity **is** the anchor plus the kind. Nothing is allocated, nothing
    is stored, and two projections of the same graph agree without having to
    consult each other -- which is what lets a cut be compared with a later
    one at all.

    Readable on purpose. A digest was needed while a unit was a connected
    component with no name of its own; a unit anchored to a spec entry has
    one, and whoever receives the ticket can place it without a lookup.
    """
    return f"{anchor}{TEST_SUFFIX if tests else ''}"


@dataclasses.dataclass(frozen=True)
class Unit:
    key: str
    # The spec entry this unit is anchored to. For a test unit, the spec
    # entry its scenarios judge -- never one of its own test identifiers.
    anchor: Identifier
    entries: tuple[Identifier, ...]
    # The write set, and the only thing a branch for this unit may edit.
    modules: tuple[str, ...]
    # Which slices this unit's entries belong to. More than one means a file
    # in it straddles a slice boundary -- reported, never refused.
    slices: tuple[str, ...] = ()
    # Total body bytes of this unit's entries. The one size signal that is
    # not already the length of a list above, and the one that matters: a
    # single entry can carry more to read than five.
    body_bytes: int = 0

    @property
    def tests(self) -> bool:
        """True when this unit holds test entries rather than spec entries.

        Read off the identifiers, never stored and never declared. A module
        claiming spec and test entries together is refused, so a unit is
        necessarily all of one kind -- and a `kind` field would be a second
        statement of what the entry set already says.
        """
        return bool(self.entries) and self.entries[0].layer == TEST

    @property
    def size(self) -> dict:
        """What a consumer reads to pick its own batch size.

        Reported, never compared against a threshold here. How much one
        session can hold depends on the model driving it, and this unit
        knows nothing about that.
        """
        return {
            "entries": len(self.entries),
            "modules": len(self.modules),
            "body_bytes": self.body_bytes,
        }

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "anchor": str(self.anchor),
            "entries": [str(i) for i in self.entries],
            "modules": list(self.modules),
            "slices": list(self.slices),
            "tests": self.tests,
            "size": self.size,
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
    # Unit key -> the keys it shares a module with. Symmetric, computed in
    # both directions, stored in neither -- as `covers`/`covered_by` are.
    #
    # Deliberately NOT an edge. Two units writing one file must not be
    # dispatched at the same time, and beyond that they need no order
    # relative to each other; inventing one where the spec states no
    # dependency would be this module scheduling. Honouring it is the
    # consumer's business, which is why it is reported separately from the
    # contract above rather than folded into it.
    overlap: dict[str, tuple[str, ...]]
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
    # Live spec entries with no live test entry deriving from them. A
    # contract nothing can falsify. The graph-level half of coverage, and
    # the reason it sits here as well as in the gate report: a person
    # deciding whether to cut is looking at exactly this screen.
    untested: tuple[Identifier, ...] = ()
    # Live test entries no module claims. The scenario is written and
    # nothing implements it yet, so it is in no unit and imposes no order --
    # which is correct rather than a gap. A test specified but not built is
    # not yet expected to pass, and ordering implementation behind one would
    # block work on a file nobody has written.
    unimplemented_tests: tuple[Identifier, ...] = ()
    # Test units nothing follows: every spec entry their scenarios judge is
    # unimplemented, so there is no implementation unit to precede. Ordinary
    # and temporary while tests run ahead of code -- and worth seeing,
    # because a test unit that stays unfollowed is testing a contract nobody
    # is building.
    unfollowed_tests: tuple[str, ...] = ()

    def by_key(self) -> dict[str, Unit]:
        return {u.key: u for u in self.units}

    def unit_of(self) -> dict[Identifier, str]:
        return {e: u.key for u in self.units for e in u.entries}

    def to_json(self) -> dict:
        return {
            "units": [u.to_json() for u in self.units],
            "edges": {k: list(v) for k, v in sorted(self.edges.items())},
            "overlap": {k: list(v) for k, v in sorted(self.overlap.items())},
            "waves": [
                {"wave": n, "units": list(w), "width": len(w)}
                for n, w in enumerate(self.schedule.waves)
            ],
            "unschedulable": list(self.schedule.unschedulable),
            "unimplemented": [str(i) for i in self.unimplemented],
            "stale_modules": list(self.stale_modules),
            "dangling": [str(i) for i in self.dangling],
            "untested": [str(i) for i in self.untested],
            "unimplemented_tests": [str(i) for i in self.unimplemented_tests],
            "unfollowed_tests": list(self.unfollowed_tests),
        }


def entry_slices(manifest: Manifest, graph: Graph) -> dict[Identifier, str]:
    """Which slice each live entry belongs to.

    A test entry has no membership of its own and never will -- its column is
    the column of the spec entry it judges, resolved here rather than stored.
    A written copy would be the one that goes stale the first time a slice
    splits, which is the argument this unit makes about every other projected
    fact.
    """
    out: dict[Identifier, str] = {}
    for name, sl in manifest.slices.items():
        for identifier in sl.members:
            out[identifier] = name
    for entry in graph.entries():
        if entry.id.layer != TEST:
            continue
        for parent in entry.derives_from:
            if parent in out:
                out[entry.id] = out[parent]
                break
    return out


# =====================================================================
# THE MODULE MAP'S OWN PROJECTIONS
#
# Every one of these is computed from the module map and the graph, and
# none of them is stored. That is the whole point: a relation written down
# twice has to be kept in step by hand, and the copy someone maintains is
# the one that goes stale. Compute both directions and there is nothing to
# keep in step -- change a module's `implements` and every view of it moves
# with it, because there was never a second thing to update.
# =====================================================================


def module_slices(
    manifest: Manifest, graph: Graph, path: str, slices: dict | None = None
) -> tuple[str, ...]:
    """Which slices a module reaches, **most-represented first**.

    A module may serve more than one, and on real data it does: one file
    here implements four capture entries and four observability ones. That
    is not a defect to refuse -- a slice is a partition of ENTRIES, and a
    file is a many-to-many pointer that was never promised to respect it.

    Ordered by how many of the module's entries come from each slice so a
    caller needing a single value can take the first without this unit
    having to assert one slice as fact. Ties break by name and stay ties:
    the worst straddler in the sample data is a dead four-four, so a
    plurality rule would not have decided the case it exists to decide.
    """
    resolved = slices if slices is not None else entry_slices(manifest, graph)
    counted: dict[str, int] = {}
    for identifier in manifest.modules.get(path, ()):  # live-ness is the
        name = resolved.get(identifier)                # caller's problem
        if name:
            counted[name] = counted.get(name, 0) + 1
    return tuple(
        name for name, _ in sorted(counted.items(), key=lambda kv: (-kv[1], kv[0]))
    )


def _relatives(manifest: Manifest, graph: Graph, path: str, want_tests: bool):
    """One traversal, walked in either direction.

    A test module's entries are test entries; step up to the contract each
    judges, then out to the files implementing it. An implementation
    module's entries are spec entries; step down to the tests judging them,
    then out to the files implementing those. Same three hops, mirrored.
    """
    hop = graph.parents if want_tests else graph.children
    out: set[str] = set()
    for identifier in manifest.modules.get(path, ()):
        for other in hop(identifier):
            if want_tests and other.layer != SPEC:
                continue
            if not want_tests and other.layer != TEST:
                continue
            out.update(manifest.implementers(other))
    out.discard(path)
    return tuple(sorted(out))


def covers(manifest: Manifest, graph: Graph, path: str) -> tuple[str, ...]:
    """For a TEST module: the implementation files holding the contracts its
    tests judge.

    A test module covering nothing is judging a contract nobody built. That
    is worth seeing and is never an error -- tests are written before the
    code, so it is the ordinary state for as long as that gap lasts.
    """
    return _relatives(manifest, graph, path, want_tests=True)


def covered_by(manifest: Manifest, graph: Graph, path: str) -> tuple[str, ...]:
    """For an IMPLEMENTATION module: the test files judging its contracts.

    The direction you actually read before changing a file, and the reason
    both directions are computed rather than one being stored and inverted
    by hand.
    """
    return _relatives(manifest, graph, path, want_tests=False)


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

    entry_slice = entry_slices(manifest, graph)

    for paths in entry_modules.values():
        paths.sort()
    return entry_modules, entry_slice, sorted(set(stale))


def _build(
    entry_modules: dict[Identifier, list[str]],
    entry_slice: dict[Identifier, str],
    graph: Graph,
) -> list[Unit]:
    """One unit per implemented spec entry, plus one per spec entry with
    built scenarios.

    A module appearing in several units is normal, and is exactly what
    `overlap` reports. It is not grouped away: grouping is what made unit
    size a property of the project's worst file rather than of the contract
    being built.

    A test unit is anchored to the spec entry its scenarios judge, never to
    one of its own identifiers. That is what makes the pairing exact without
    being one-to-one -- a shared fixture serving three spec entries lands in
    three test units and glues none of their implementations together.
    """
    tests_of: dict[Identifier, list[Identifier]] = {}
    for identifier in entry_modules:
        if identifier.layer != TEST:
            continue
        for parent in graph.parents(identifier):
            tests_of.setdefault(parent, []).append(identifier)

    def sized(entries: tuple[Identifier, ...]) -> int:
        total = 0
        for identifier in entries:
            entry = graph.get(identifier)
            if entry is not None:
                total += len(entry.body.encode("utf-8"))
        return total

    anchors = sorted(
        {i for i in entry_modules if i.layer == SPEC} | set(tests_of),
        key=sort_key,
    )
    units: list[Unit] = []
    for anchor in anchors:
        # Tests first, so a unit and the one that must precede it sit next to
        # each other in every listing that does not re-sort.
        for tests in (True, False):
            if tests:
                held = tuple(sorted(tests_of.get(anchor, ()), key=sort_key))
            else:
                held = (anchor,) if anchor in entry_modules else ()
            if not held:
                continue
            modules = tuple(sorted({m for e in held for m in entry_modules[e]}))
            slices = tuple(
                sorted({entry_slice[e] for e in held if e in entry_slice})
            )
            units.append(
                Unit(
                    key=unit_key(anchor, tests),
                    anchor=anchor,
                    entries=held,
                    modules=modules,
                    slices=slices,
                    body_bytes=sized(held),
                )
            )
    return units


def overlap(units: list[Unit]) -> dict[str, tuple[str, ...]]:
    """Which units share a module, and so may not be dispatched at once.

    Symmetric and computed in both directions, stored in neither. Every unit
    is a key even when it overlaps nothing, so a consumer can look one up
    without having to know whether the absence means "no overlap" or "not
    computed".
    """
    by_module: dict[str, list[str]] = {}
    for unit in units:
        for path in unit.modules:
            by_module.setdefault(path, []).append(unit.key)

    out: dict[str, set[str]] = {u.key: set() for u in units}
    for sharers in by_module.values():
        if len(sharers) < 2:
            continue
        for key in sharers:
            out[key].update(k for k in sharers if k != key)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def wave_view(units: list[Unit], edges: dict[str, tuple[str, ...]]) -> Schedule:
    """Waves as a person reads them, with test units pulled forward.

    A test unit waits on nothing, so longest-path puts it in wave 0. True,
    and useless to read: forty test tickets at the front of a project says
    nothing about when any of them is wanted, and writing a scenario for a
    contract seven waves out invites doing it before the contract settles.

    So a test unit is shown immediately before the earliest unit that
    follows it. Presentation only -- the edges do not move, and a consumer
    honouring edges rather than waves sees no difference at all.
    """
    base = schedule_edges(edges)
    depth = base.wave_of()
    if not depth:
        return base

    followers: dict[str, list[str]] = {}
    for key, deps in edges.items():
        for dep in deps:
            followers.setdefault(dep, []).append(key)

    moved = dict(depth)
    for unit in units:
        after = [depth[f] for f in followers.get(unit.key, ()) if f in depth]
        if unit.tests and after and unit.key in moved:
            moved[unit.key] = min(after) - 1

    floor = min(moved.values())
    moved = {k: v - floor for k, v in moved.items()}
    top = max(moved.values())
    return Schedule(
        waves=tuple(
            tuple(sorted(k for k, v in moved.items() if v == number))
            for number in range(top + 1)
        ),
        unschedulable=base.unschedulable,
    )


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

    # The second source, and the only one that is not a depends_on: the unit
    # implementing S*X follows the test unit for S*X. Documentation first,
    # tests second, code last.
    #
    # Computed here rather than read off `derives_from`, which means
    # justification and only justification -- keeping the two apart is what
    # makes unit dependency computable instead of guesswork. A second source
    # costs these lines; redefining an edge would cost the property.
    #
    # Both units share an anchor, so this is a lookup rather than a search.
    # The pairing is still not one-to-one and nothing here assumes it is: a
    # shared fixture serving several spec entries is a member of each of
    # their test units, so each implementation unit waits for its own. That
    # fans out and merges nothing.
    #
    # A spec entry whose scenarios have no module has no test unit, and so
    # imposes no order. That is the point of the rule rather than a hole in
    # it: a scenario written but not yet built is not yet expected to pass,
    # and ordering implementation behind one would block work on a file
    # nobody has written.
    for unit in units:
        if unit.tests:
            continue
        mate = unit_key(unit.anchor, tests=True)
        if mate in out:
            out[unit.key].add(mate)

    return (
        {k: tuple(sorted(v)) for k, v in out.items()},
        sorted(dangling, key=sort_key),
    )


def project(manifest: Manifest, graph: Graph) -> Projection:
    """Compute the work units of a project.

    Deterministic and side-effect free. An empty module map yields no units
    and every spec entry unimplemented -- described rather than refused,
    because a person who cannot cut needs to be able to see why.
    """
    entry_modules, entry_slice, stale = _invert(manifest, graph)
    units = _build(entry_modules, entry_slice, graph)
    edges, dangling = _edges(units, graph)

    unimplemented = tuple(
        sorted(
            (e.id for e in graph.entries()
             if e.id.layer == SPEC and e.id not in entry_modules),
            key=sort_key,
        )
    )
    unimplemented_tests = tuple(
        sorted(
            (e.id for e in graph.entries()
             if e.id.layer == TEST and e.id not in entry_modules),
            key=sort_key,
        )
    )
    untested = tuple(
        sorted(
            (e.id for e in graph.entries()
             if e.id.layer == SPEC
             and not any(c.layer == TEST for c in graph.children(e.id))),
            key=sort_key,
        )
    )
    # A test unit nothing follows. Read off the edges that were just built
    # rather than recomputed, so the two can never disagree.
    followed = {k for targets in edges.values() for k in targets}
    unfollowed_tests = tuple(
        sorted(u.key for u in units if u.tests and u.key not in followed)
    )
    return Projection(
        units=tuple(units),
        edges=edges,
        overlap=overlap(units),
        schedule=wave_view(units, edges),
        unimplemented=unimplemented,
        stale_modules=tuple(stale),
        dangling=tuple(sorted(dangling, key=sort_key)),
        untested=untested,
        unimplemented_tests=unimplemented_tests,
        unfollowed_tests=unfollowed_tests,
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
    # What verification looked like at the moment work went out.
    #
    # A cut recorded `unimplemented` and dropped these, so "which contracts
    # could nothing catch failing when this was handed out" was answerable
    # only from the live graph -- which has moved on by the time anyone
    # asks. That is the one question a stored cut exists to answer, and on
    # the half of it that concerns tests it was silent. Defaulted empty, so
    # cuts written before this reload as what they were: unsaid, not zero.
    untested: tuple[Identifier, ...] = ()
    unimplemented_tests: tuple[Identifier, ...] = ()
    unfollowed_tests: tuple[str, ...] = ()
    # An order that was real and could not be drawn. Recorded for the same
    # reason as the three above: a cut answers what was true when work went
    # out, and "this unit should have waited for something, and the
    # projection could not say what" is exactly the kind of thing nobody
    # reconstructs afterwards from a graph that has moved on.
    dangling: tuple[Identifier, ...] = ()

    def by_key(self) -> dict[str, Unit]:
        return {u.key: u for u in self.units}

    def to_json(self) -> dict:
        schedule = wave_view(list(self.units), self.edges)
        return {
            "seq": self.seq,
            "at": self.at,
            "note": self.note,
            "units": [u.to_json() for u in self.units],
            "edges": {k: list(v) for k, v in sorted(self.edges.items())},
            # Recomputed from the units this cut stores, as the waves above
            # are. A relation over stored bodies is not itself worth storing.
            "overlap": {
                k: list(v)
                for k, v in sorted(overlap(list(self.units)).items())
            },
            "waves": [
                {"wave": n, "units": list(w), "width": len(w)}
                for n, w in enumerate(schedule.waves)
            ],
            "unschedulable": list(schedule.unschedulable),
            "unimplemented": [str(i) for i in self.unimplemented],
            "stale_modules": list(self.stale_modules),
            "untested": [str(i) for i in self.untested],
            "unimplemented_tests": [str(i) for i in self.unimplemented_tests],
            "unfollowed_tests": list(self.unfollowed_tests),
            "dangling": [str(i) for i in self.dangling],
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
        "anchor": str(unit.anchor),
        "entries": [str(i) for i in unit.entries],
        "modules": list(unit.modules),
        "slices": list(unit.slices),
        "body_bytes": unit.body_bytes,
    }


def _unit_from_body(key: str, body: dict) -> Unit:
    entries = tuple(parse(i) for i in body.get("entries", ()))
    stored = body.get("anchor")
    if stored is None and not entries:
        raise CutError(
            f"work unit {key} has neither an anchor nor entries, so there is "
            "no write set to hand out"
        )
    return Unit(
        key=key,
        # A cut written before units were anchored carries no anchor. Its
        # first entry is the closest true thing, and an old cut is a record
        # of what went out rather than something to re-derive -- refusing to
        # read history would lose the audit trail to gain nothing.
        anchor=parse(stored) if stored else entries[0],
        entries=entries,
        modules=tuple(body.get("modules", ())),
        slices=tuple(body.get("slices", ())),
        body_bytes=int(body.get("body_bytes", 0)),
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
                untested=tuple(parse(i) for i in raw.get("untested", ())),
                unimplemented_tests=tuple(
                    parse(i) for i in raw.get("unimplemented_tests", ())
                ),
                unfollowed_tests=tuple(raw.get("unfollowed_tests", ())),
                dangling=tuple(parse(i) for i in raw.get("dangling", ())),
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
        "untested": [str(i) for i in projection.untested],
        "unimplemented_tests": [
            str(i) for i in projection.unimplemented_tests
        ],
        "unfollowed_tests": list(projection.unfollowed_tests),
        "dangling": [str(i) for i in projection.dangling],
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
        untested=projection.untested,
        unimplemented_tests=projection.unimplemented_tests,
        unfollowed_tests=projection.unfollowed_tests,
        dangling=projection.dangling,
    )


class _Find:
    """Union-find over strings, used by `drift` and nowhere else.

    It grouped module paths into work units while a unit was a connected
    component. A unit is now anchored to a spec entry and has a stable name,
    so nothing in the projection needs this; what still does is comparing a
    stored cut against the live graph, where the "shares an entry" relation
    between two *generations* of units is genuinely a grouping problem.
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
