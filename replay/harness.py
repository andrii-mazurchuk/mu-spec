"""Replay a project's exported history through mu-spec's gates, and score them.

A snapshot directory is what a node export holds for one project: every
entry ever written (`entries/`), the lifecycle log (`events.json`), the cuts,
and the final gate, wave and unit reports. From that the harness rebuilds the
graph and manifest *as they stood* after every graph-changing event, runs a
registry of gate functions over each state, and scores each gate against an
oracle of hand-mapped faults: which it caught, at which step first, and what
else it flagged.

This is test tooling, not a caller of the live unit. It builds every graph in
memory and calls mu-spec's library functions directly; it never opens a
store, never speaks HTTP and writes nothing but the report it is asked for.
The HTTP path could not replay history anyway -- the gates refuse the very
states a replay exists to look at.

What an export does not hold, and how it is recovered:

- An entry's birth is the `derivation` event that lists it; its retirement is
  its successor's birth (`superseded_by`, inverted into `supersedes`).
- A live read carries an edge across an interface-preserving supersession,
  and the export was a live read. Each edge target is walked back to the
  version that existed when the child was born.
- Slice membership is the derivation's slice, then any `slicing` move. A
  behaviour entry is never derived into a slice, so it takes the slice most
  of its architecture children are in. A `classification` event is read
  past: the cross-cutting type it set was removed (D6), and no gate reads it.
- There is no module history. Module ownership is read from each spec
  body's `Module:` line, plus the final `modules.json` for the entries live
  at the export, taken as declared at each entry's birth.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Callable, Iterable

from mu_spec import service, waves
from mu_spec.body import legacy_imports
from mu_spec.gates import (
    BAD_IMPORT,
    NO_IMPORTS_LINE,
    RETIRED_MENTION,
    TWO_SUCCESSORS,
    UNLINKED_MENTION,
    UNDEFINED_NAME,
    UNKNOWN_MEMBER,
    UNNAMED_FIXTURE,
    UNSERVED,
    UNTESTED,
    imports,
    two_successors,
    undefined_names,
)
from mu_spec.graph import Entry, Graph
from mu_spec.slice_gates import (
    BAD_MODULE_IMPORT,
    NO_MODULE,
    SPLIT_OWNERSHIP,
    SWEEP_COLLISION,
    UNDECLARED_PATH,
    module_gates,
)
from mu_spec.waves import same_wave_edges
from mu_spec.identifiers import BEHAVIOUR, SPEC, TEST, UNSLICED, parse
from mu_spec.storage import Manifest, Slice

# The lifecycle events that change the graph or the manifest. Every other kind
# (requests, issues, cuts) leaves both as they were. A `correction` is not
# one: an amendment logs its new entries and the ones they retire as two
# events a millisecond apart, and the retirement is read off the successor's
# birth -- so a step after the correction would repeat the step before it.
STRUCTURAL = ("derivation", "slicing")
EDGES = ("derives_from", "depends_on")


class SnapshotError(ValueError):
    """The snapshot or the oracle cannot be read the way the harness needs."""


@dataclasses.dataclass(frozen=True)
class Step:
    seq: int  # the event this state follows
    at: float
    kind: str
    cut: int  # the latest cut at or before this state; 0 before the first
    refs: tuple[str, ...] = ()


@dataclasses.dataclass
class History:
    root: Path
    project: str
    entries: dict[str, Entry]  # canonical id -> entry, edges as written
    born: dict[str, float]
    slice_born: dict[str, str | None]
    moves: dict[str, list[tuple[float, str]]]  # id -> [(at, into)]
    children: dict[str, list[str]]  # parent id -> child ids, edges as written
    events: list[dict]
    cuts: list[dict]
    notes: list[str]
    order: list[str] = dataclasses.field(default_factory=list)  # ids by birth
    modules: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"cannot read {path}: {exc}") from exc


def _events(data) -> list[dict]:
    return data["events"] if isinstance(data, dict) else data


def canon(text: str) -> str:
    return str(parse(text))


# At a line's start, or opening a sentence mid-paragraph (`**Out of band.**
# Module: ...`, S-229).
_MODULE_LINE = re.compile(r"(?m)(?:^\W*(?:Modules?|Files?)|(?<=[.*] )Modules?)\s*:\s*(.*)$")


def module_paths(body: str) -> tuple[str, ...]:
    """The backticked paths in the first sentence of a `Module:` line."""
    paths: list[str] = []
    for line in _MODULE_LINE.findall(body.replace("\\n", "\n")):
        sentence = re.split(r"\.(?:\s|$)", line, maxsplit=1)[0]
        paths += [
            p for p in re.findall(r"`([^`\s]+)`", sentence) if "/" in p or "." in p
        ]
    return tuple(dict.fromkeys(paths))


def load(root: Path | str) -> History:
    root = Path(root)
    raws: dict[str, dict] = {}
    files = sorted((root / "entries").glob("*.json"))
    for path in files:
        raw = _read(path)
        ident = canon(raw["id"])
        if ident in raws and raws[ident] != raw:
            raise SnapshotError(f"{ident} is saved twice with different contents ({path.name})")
        raws[ident] = raw

    events = sorted(_events(_read(root / "events.json")), key=lambda e: (e["at"], e["seq"]))
    born: dict[str, float] = {}
    slice_born: dict[str, str | None] = {}
    moves: dict[str, list[tuple[float, str]]] = collections.defaultdict(list)
    for event in events:
        if event["kind"] == "derivation":
            for ref in event["refs"]:
                born.setdefault(canon(ref), event["at"])
                slice_born.setdefault(canon(ref), event["facts"].get("slice"))
        elif event["kind"] == "slicing":
            for ref in event["refs"]:
                moves[canon(ref)].append((event["at"], event["facts"]["into"]))

    unborn = sorted(set(raws) - set(born))
    if unborn:
        raise SnapshotError(f"no derivation event lists {', '.join(unborn[:10])}")

    successor = {k: canon(r["superseded_by"]) for k, r in raws.items() if r.get("superseded_by")}
    predecessor = {new: old for old, new in successor.items()}
    corrected = {
        canon(ref): e["at"] for e in events if e["kind"] == "correction" for ref in e["refs"]
    }
    skew = max(
        (abs(corrected[old] - born[new]) for old, new in successor.items() if old in corrected),
        default=0.0,
    )

    def as_written(child: str, target: str) -> str:
        """Walk a redirected edge back to the version alive at the child's birth."""
        target = canon(target)
        while target in predecessor and born.get(target, 0) > born[child]:
            target = predecessor[target]
        return target

    entries: dict[str, Entry] = {}
    children: dict[str, list[str]] = collections.defaultdict(list)
    forward = 0
    for ident, raw in raws.items():
        edges = {kind: tuple(as_written(ident, t) for t in raw.get(kind, ())) for kind in EDGES}
        forward += sum(born.get(t, 0) > born[ident] for ts in edges.values() for t in ts)
        for parent in edges["derives_from"]:
            children[parent].append(ident)
        entries[ident] = Entry(
            id=parse(ident),
            **{kind: tuple(map(parse, ts)) for kind, ts in edges.items()},
            title=raw.get("title", ""),
            purpose=raw.get("purpose", ""),
            body=raw.get("body", ""),
            supersedes=parse(predecessor[ident]) if ident in predecessor else None,
            preserves_interface=bool(raw.get("preserves_interface")),
        )

    gates = root / "gates.json"
    project = _read(gates).get("project", "replay") if gates.exists() else "replay"
    cuts = _read(root / "cuts.json")
    history = History(
        root=root,
        project=project,
        entries=entries,
        born=born,
        slice_born=slice_born,
        moves=dict(moves),
        children=dict(children),
        events=events,
        cuts=sorted(cuts["cuts"] if isinstance(cuts, dict) else cuts, key=lambda c: c["at"]),
        notes=[
            f"{len(files)} entry files, {len(entries)} unique ids",
            f"{len(predecessor)} supersessions, retired at the successor's birth "
            f"(at most {skew:.1f}s from the correction event)",
            f"{forward} edges still point at an entry born after the child",
        ],
    )
    history.order = sorted(entries, key=lambda k: born[k])
    history.modules = {k: module_paths(e.body) for k, e in entries.items() if e.id.layer == SPEC}
    # The node's final module map, for the entries live at the export, taken
    # as declared at each entry's birth. It is the only source for a test
    # entry, whose body names no file, and it catches a spec declared through
    # `declare_module` without a `Module:` line saying so. A retired entry's
    # declarations are unknown, so its `Module:` line stands alone. A file the
    # map gives to test entries is never a spec's, whatever a line claims.
    exported = root / "modules.json"
    if exported.exists():
        rows = _read(exported).get("modules", ())
        tests_own = {
            r["path"] for r in rows
            if any(canon(i) in entries and entries[canon(i)].id.layer == TEST
                   for i in r.get("implements", ()))
        }
        history.modules = {
            k: tuple(p for p in ps if p not in tests_own) for k, ps in history.modules.items()
        }
        for row in rows:
            for ident in map(canon, row.get("implements", ())):
                if ident in entries and row["path"] not in history.modules.get(ident, ()):
                    history.modules[ident] = history.modules.get(ident, ()) + (row["path"],)
    return history


def steps(history: History) -> list[Step]:
    """One state after each graph-changing event, deduplicated by time."""
    cut_times = [c["at"] for c in history.cuts]
    out: list[Step] = []
    for event in history.events:
        if event["kind"] not in STRUCTURAL:
            continue
        step = Step(
            seq=event["seq"],
            at=event["at"],
            kind=event["kind"],
            cut=bisect.bisect_right(cut_times, event["at"]),
            refs=tuple(event["refs"]),
        )
        if out and out[-1].at == step.at:
            out[-1] = step
        else:
            out.append(step)
    return out


def step_index(all_steps: list[Step], seq: int) -> int:
    """The state as it stood at event `seq`: the last step at or before it."""
    index = bisect.bisect_right([s.seq for s in all_steps], seq) - 1
    if index < 0:
        raise SnapshotError(f"event {seq} precedes the first step")
    return index


def _slice_at(history: History, ident: str, t: float) -> str | None:
    owner = history.slice_born.get(ident)
    for at, into in history.moves.get(ident, ()):
        if at <= t:
            owner = into
    return owner


def build(history: History, t: float) -> tuple[Graph, Manifest]:
    """The graph and manifest as they stood at time `t`."""
    count = bisect.bisect_right([history.born[k] for k in history.order], t)
    known = history.order[:count]
    graph = Graph(history.entries[k] for k in known)

    manifest = Manifest(project=history.project)
    for ident in known:
        layer = history.entries[ident].id.layer
        if layer in UNSLICED:
            continue
        owner = _slice_at(history, ident, t)
        if owner is None and layer == BEHAVIOUR:
            votes = [
                _slice_at(history, c, t)
                for c in history.children.get(ident, ())
                if history.born[c] <= t
            ]
            votes = sorted(v for v in votes if v)
            owner = max(sorted(set(votes)), key=votes.count) if votes else None
        if owner is not None:
            manifest.slices.setdefault(owner, Slice(name=owner)).members.add(parse(ident))

    for entry in graph.entries():
        for path in history.modules.get(str(entry.id), ()):
            manifest.modules.setdefault(path, set()).add(entry.id)
    return graph, manifest


# -- gates -------------------------------------------------------------------

Gate = Callable[[Graph, Manifest, Graph, Manifest], Iterable[str]]

_last: list = [None, None, None]


def _report(graph: Graph, manifest: Manifest) -> dict:
    """The gate report, computed once per state however many gates read it."""
    if _last[0] is not graph or _last[1] is not manifest:
        _last[:] = [graph, manifest, service._gate_report(graph, manifest)]
    return _last[2]


def sound(graph, manifest, prev_graph, prev_manifest) -> set[str]:
    """Today's blocking gates: what would have refused the amendment."""
    report = _report(graph, manifest)
    return {f["id"] for f in report["findings"] if f["kind"] in service._BLOCKING} | {
        f"slice:{f['slice']}" for f in report["slice_findings"]
    }


def reported(kind: str) -> Gate:
    """One kind of today's non-blocking report, on its own row.

    Split rather than pooled as "complete" because `unserved` and `untested`
    fire on every entry from its birth until its children are derived. Pooled,
    that timing reads as detection of whatever fault was also present at
    birth; on its own row, a reader can see what it is."""
    def gate(graph, manifest, prev_graph, prev_manifest) -> set[str]:
        return {f["id"] for f in _report(graph, manifest)["findings"] if f["kind"] == kind}
    return gate


# The legacy reader feeds M2 here and nowhere else. DARK predates the D4
# `Imports:` line, so the strict reader -- the only one live admission uses,
# and the only one that ever refuses -- finds nothing to check in it. The
# prose reader exists to score the gate against DARK's history.
def _legacy(entry: Entry):
    return legacy_imports(entry.body) or None


def m2(graph, manifest, prev_graph, prev_manifest) -> set[str]:
    return {
        str(f.id)
        for f in imports(graph, read=_legacy) + module_gates(manifest, graph, read=_legacy)
        if f.kind in (BAD_IMPORT, BAD_MODULE_IMPORT)
    }


def m3(graph, manifest, prev_graph, prev_manifest) -> set[str]:
    return {
        str(f.id)
        for f in module_gates(manifest, graph)
        if f.kind in (SPLIT_OWNERSHIP, UNDECLARED_PATH)
    }


def m4(graph, manifest, prev_graph, prev_manifest) -> set[str]:
    """`undefined_name` with the legacy reader, so DARK's prose imports count."""
    return {str(f.id) for f in undefined_names(graph, read=_legacy)}


def m5(graph, manifest, prev_graph, prev_manifest) -> set[str]:
    """`same_wave_edge`: the waves the step's entries were written into."""
    return {str(f.id) for f in same_wave_edges(prev_graph, prev_manifest, graph, manifest)}


def successors(graph, manifest, prev_graph, prev_manifest) -> set[str]:
    """Always empty here, by construction: the export records one
    `superseded_by` per entry, so a replay cannot hold two claims."""
    return {str(f.id) for f in two_successors(graph)}


# Kinds added after the DARK export was taken. Its gates.json was computed
# without them, so the self-check compares the report with them taken out --
# the check is that the reconstruction is right, not that old gates agree
# with new ones.
NEWER_KINDS = (
    BAD_IMPORT, NO_IMPORTS_LINE, TWO_SUCCESSORS,
    BAD_MODULE_IMPORT, SPLIT_OWNERSHIP, UNDECLARED_PATH,
    RETIRED_MENTION, UNLINKED_MENTION,
    UNDEFINED_NAME, UNKNOWN_MEMBER, NO_MODULE, SWEEP_COLLISION, UNNAMED_FIXTURE,
)


def as_exported(report: dict) -> dict:
    findings = [f for f in report["findings"] if f["kind"] not in NEWER_KINDS]
    sound = not any(f["kind"] in service._BLOCKING for f in findings) and not report["slice_findings"]
    return {
        **report,
        "findings": findings,
        "sound": sound,
        "complete": not findings,
        "clean": not findings and not report["slice_findings"],
    }


# A new gate ticket adds one line here, named after the ticket (see README).
REGISTRY: dict[str, Gate] = {
    "sound": sound,
    UNSERVED: reported(UNSERVED),
    UNTESTED: reported(UNTESTED),
    "M2": m2,
    "M3": m3,
    TWO_SUCCESSORS: successors,
    "M4": m4,
    "M5": m5,
    "M6": reported(UNKNOWN_MEMBER),
    "M7": reported(SWEEP_COLLISION),
    "M10": reported(NO_MODULE),
    "M22": reported(UNNAMED_FIXTURE),
}


def run(history: History, registry: dict[str, Gate]) -> dict[str, list[set[str]]]:
    """Every gate's flagged ids, one set per step."""
    flags: dict[str, list[set[str]]] = {name: [] for name in registry}
    prev = (Graph(()), Manifest(project=history.project))
    for step in steps(history):
        graph, manifest = build(history, step.at)
        for name, gate in registry.items():
            flags[name].append(set(gate(graph, manifest, *prev)))
        prev = (graph, manifest)
    return flags


# -- the oracle and the score ------------------------------------------------


SLICE_TOKEN = "slice:"


def _key(raw: str) -> str:
    """An expected or flagged id: an entry identifier, or `slice:<name>` for a
    finding about a slice rather than an entry."""
    return raw if raw.startswith(SLICE_TOKEN) else canon(raw)


def check_oracle(history: History, cases: list[dict]) -> None:
    """Every expected id exists, and is live, at the step it is said to be visible."""
    at_of = {e["seq"]: e["at"] for e in history.events}
    for case in cases:
        name = case["finding_id"]
        seq = case["earliest_step_visible"]
        if seq not in at_of:
            raise SnapshotError(f"{name}: no event {seq}")
        t = at_of[seq]
        graph, manifest = build(history, t)
        for raw in case["expected_ids"]:
            if raw.startswith(SLICE_TOKEN):
                if raw[len(SLICE_TOKEN):] not in manifest.slices:
                    raise SnapshotError(f"{name}: no {raw} at event {seq}")
                continue
            ident = canon(raw)
            if ident not in history.entries:
                raise SnapshotError(f"{name}: {ident} is not in the snapshot")
            if history.born[ident] > t:
                raise SnapshotError(f"{name}: {ident} is not born by event {seq}")
            if graph.superseded_by(parse(ident)) is not None:
                raise SnapshotError(f"{name}: {ident} is already retired at event {seq}")


def score(all_steps: list[Step], flags: dict[str, list[set[str]]], cases: list[dict]) -> dict:
    """A case is caught by a gate at the first step, at or after the one it is
    visible from, where the gate flags every one of its expected ids at once.
    A false positive is an id the gate flagged that no case expects."""
    expected_anywhere = {_key(i) for c in cases for i in c["expected_ids"]}
    out: dict = {}
    for gate, per_step in flags.items():
        rows = []
        by_ticket: dict[str, list[int]] = {}
        for case in cases:
            want = {_key(i) for i in case["expected_ids"]}
            start = step_index(all_steps, case["earliest_step_visible"])
            hit = next((i for i in range(start, len(all_steps)) if want <= per_step[i]), None)
            rows.append({
                "finding_id": case["finding_id"],
                "gate_ticket": case["gate_ticket"],
                "first_step": all_steps[hit].seq if hit is not None else None,
                "first_cut": all_steps[hit].cut if hit is not None else None,
            })
            tally = by_ticket.setdefault(case["gate_ticket"], [0, 0])
            tally[0] += hit is not None
            tally[1] += 1
        caught = sum(r["first_step"] is not None for r in rows)
        out[gate] = {
            "caught": caught,
            "missed": len(rows) - caught,
            "by_ticket": dict(sorted(by_ticket.items(), key=lambda kv: _ticket_key(kv[0]))),
            "false_positives": sorted(set().union(*per_step) - expected_anywhere),
            "flagged_steps": sum(bool(ids) for ids in per_step),
            "cases": rows,
        }
    return out


def _ticket_key(ticket: str):
    digits = "".join(ch for ch in ticket if ch.isdigit())
    return (ticket.rstrip("0123456789"), int(digits) if digits else 0)


# -- the self-check ----------------------------------------------------------


def _live_st(graph: Graph) -> set[str]:
    return {str(e.id) for e in graph.entries() if e.id.layer in (SPEC, TEST)}


def self_check(history: History) -> list[str]:
    """The final replayed state must be what the node reported. Any mismatch
    means the reconstruction is wrong, and every score built on it is too."""
    problems: list[str] = []
    root = history.root
    final_steps = steps(history)
    if not final_steps:
        return ["the snapshot has no graph-changing events"]
    graph, manifest = build(history, final_steps[-1].at)

    report = as_exported(service._gate_report(graph, manifest))
    if not (root / "gates.json").exists():
        problems.append("gates.json is missing")
    else:
        node = _read(root / "gates.json")
        for key in ("sound", "complete", "clean", "findings", "slice_findings"):
            if report[key] != node.get(key):
                problems.append(f"gates.json {key}: node {str(node.get(key))[:200]} != replay {str(report[key])[:200]}")

    if not (root / "waves.json").exists():
        problems.append("waves.json is missing")
    else:
        node = _read(root / "waves.json")
        schedule = waves.schedule(manifest, graph)
        want = [tuple(w["slices"]) for w in node.get("waves", ())]
        if list(schedule.waves) != want:
            problems.append(f"waves.json waves: node {want} != replay {list(schedule.waves)}")
        if list(schedule.unschedulable) != list(node.get("unschedulable", ())):
            problems.append(f"waves.json unschedulable: node {node.get('unschedulable')} != replay {list(schedule.unschedulable)}")

    if not (root / "units.json").exists():
        problems.append("units.json is missing")
    else:
        node = _read(root / "units.json")
        for key in ("cut", "live"):
            if key not in node:
                continue
            at = node[key].get("at", final_steps[-1].at) if key == "cut" else final_steps[-1].at
            want = {canon(i) for u in node[key]["units"] for i in u["entries"]}
            have = _live_st(build(history, float(at))[0])
            if want != have:
                problems.append(
                    f"units.json {key}: only in node {sorted(want - have)[:10]}, "
                    f"only in replay {sorted(have - want)[:10]}"
                )
    return problems


# -- the CLI -----------------------------------------------------------------


def _print_summary(history, all_steps, scores) -> None:
    print(f"snapshot {history.root} ({history.project}): {len(all_steps)} steps, "
          f"{len(history.cuts)} cuts; " + "; ".join(history.notes))
    tickets = sorted({t for s in scores.values() for t in s["by_ticket"]}, key=_ticket_key)
    print(f"{'gate':<16}{'caught':>8}  " + "  ".join(f"{t:>6}" for t in tickets)
          + "   false+  flagged steps  flagged cuts")
    for gate, s in scores.items():
        cells = "  ".join(f"{'%d/%d' % tuple(s['by_ticket'][t]):>6}" for t in tickets)
        total = s["caught"] + s["missed"]
        print(f"{gate:<16}{s['caught']:>4}/{total:<3}  {cells}   {len(s['false_positives']):>6}"
              f"  {s['flagged_steps']:>13}  {len(s['flagged_cuts']):>12}")
        for row in s["cases"]:
            if row["first_step"] is not None:
                print(f"    caught {row['finding_id']} ({row['gate_ticket']}) at event "
                      f"{row['first_step']}, cut {row['first_cut']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.harness", description=__doc__.split("\n")[0])
    parser.add_argument("snapshot_dir")
    parser.add_argument("oracle")
    parser.add_argument("--out", help="write the full report here as JSON")
    args = parser.parse_args(argv)

    try:
        history = load(args.snapshot_dir)
        problems = self_check(history)
        if problems:
            print("SELF-CHECK FAILED -- the replay does not reproduce the node's final state:")
            for problem in problems:
                print("  " + problem)
            return 2
        oracle = _read(Path(args.oracle))
        cases = oracle["cases"] if isinstance(oracle, dict) else oracle
        check_oracle(history, cases)
    except SnapshotError as exc:
        print(f"error: {exc}")
        return 2

    all_steps = steps(history)
    flags = run(history, REGISTRY)
    scores = score(all_steps, flags, cases)
    # The state each cut was taken from: what a gate would have said at the
    # moments work was actually issued.
    at_cut = {
        c["seq"]: bisect.bisect_right([s.at for s in all_steps], c["at"]) - 1
        for c in history.cuts
    }
    for gate, s in scores.items():
        s["flagged_cuts"] = [c for c, i in at_cut.items() if i >= 0 and flags[gate][i]]
    _print_summary(history, all_steps, scores)
    if args.out:
        Path(args.out).write_text(json.dumps({
            "snapshot": str(history.root),
            "project": history.project,
            "steps": len(all_steps),
            "notes": history.notes,
            "self_check": [],
            "gates": scores,
        }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
