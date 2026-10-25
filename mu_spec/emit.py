"""A cut becomes issues on a tracker, and stays in step with it.

This is the only module in the unit that causes something to happen outside
it, and the boundary it sits on is worth stating precisely. mu-spec **creates**
tickets -- that is its purpose -- and never **acts on** them. A Ship makes the
tracker say what the cut says: it creates, edits in place and withdraws, but
only issues nobody has picked up, and only because a person pressed it. It
reads an issue only to avoid destroying somebody else's state while doing so.
It never comments, never reopens, never reacts to a PR. The protocol is
docs/TICKETS.md.

**Two passes, not one.** A dependency needs the blocker's issue *id*, which
does not exist until the blocker has been created. Creating in dependency order
does not avoid that: the first unit created still has nothing to point at. So
every issue is created, then every edge is wired.

**Order is still dependency order**, though nothing requires it to be. Issue
numbers then ascend with the build order, so a person scanning the list sees a
test unit immediately above the work it precedes instead of interleaved noise.

**Idempotent per unit, not per cut.** The log records which unit became which
issue, and a re-run skips those. Refusing the whole cut instead would be
simpler and would leave a partially-emitted cut permanently unfinishable --
which is the one way a duplicate guard can be worse than none.

Nothing here raises. Every failure lands in the result against the unit it
belongs to, because a run of a hundred and forty-four units has to be readable
as a pass/fail list rather than as one exception about the first thing that
went wrong.
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from mu_spec.body import strip_provenance
from mu_spec.github import GitHubError
from mu_spec.render import BODY_LIMIT, LABEL_NAMESPACE, render
from mu_spec.identifiers import ALL_LAYERS, LEGACY_SEPARATOR, SEPARATOR, SEPARATOR_CLASS
from mu_spec.units import TEST_SUFFIX, anchor_of, canonical_key, is_test_key


# Every line in the log carries a kind. An emission omits it, because the
# log predates there being more than one kind and rewriting history to add a
# field is precisely what this unit does not do.
ROLLBACK = "rollback"

# The reason this unit closes an issue with, and the only one it may
# overwrite. `completed` is somebody else's word and is never touched.
WITHDRAWN = "not_planned"

# The `keys` an emission record says its unit keys are: lineage roots.
LINEAGE = "lineage"

# What GitHub says when asked to attach a sub-issue it already holds. The
# exact wording is unverified; both words have been seen in its 422s.
_ALREADY_SUB = re.compile(r"duplicate|already", re.I)


@dataclasses.dataclass(frozen=True)
class Emission:
    """One run. Append-only, like everything else this unit records."""

    seq: int
    at: float
    cut_seq: int
    repo: str
    # unit key -> {"id", "number", "url"}
    issues: dict[str, dict]
    # The edges actually wired, as "blocked<-blocker" pairs, so a re-run does
    # not ask GitHub to declare a dependency it already holds.
    wired: tuple[str, ...] = ()
    # Whether the keys are lineage roots (M13, D1). Written as `"keys":
    # "lineage"`; a record without it predates them, and so does its project.
    lineage: bool = False

    def to_json(self) -> dict:
        return {
            "seq": self.seq,
            "at": self.at,
            "cut_seq": self.cut_seq,
            "repo": self.repo,
            "issues": self.issues,
            "wired": list(self.wired),
            **({"keys": LINEAGE} if self.lineage else {}),
        }


@dataclasses.dataclass(frozen=True)
class Rollback:
    """One withdrawal. Appended beside the emission it undoes, never replacing
    it: what was created and then withdrawn is two facts, and a log that kept
    only the second could not answer why issue #57 is closed.

    `closed` is unit key -> issue number, and it is what makes the emitted
    units emittable again -- the duplicate guard subtracts it. `failed` holds
    the ones GitHub would not close, and they are deliberately NOT subtracted:
    an issue still open on the tracker must not be recreated, or the rollback
    turns one stray issue into two.
    """

    seq: int
    at: float
    cut_seq: int
    repo: str
    undone: tuple[int, ...]
    closed: dict[str, int]
    failed: tuple[dict, ...] = ()

    def to_json(self) -> dict:
        return {
            "kind": ROLLBACK,
            "seq": self.seq,
            "at": self.at,
            "cut_seq": self.cut_seq,
            "repo": self.repo,
            "undone": list(self.undone),
            "closed": self.closed,
            "failed": list(self.failed),
        }


class EmissionError(ValueError):
    """An emission log that cannot be read back.

    A hard error, and deliberately not degraded to "nothing has been emitted".
    That reading would create every issue a second time, which makes this the
    one place in this feature where absence must not be treated as normal.
    """


def read_log(path: Path) -> tuple[tuple[Emission, ...], tuple[Rollback, ...]]:
    """Both kinds, in one pass. Strict about both: a line that cannot be read
    is a hard error for the reason `EmissionError` gives."""
    emissions = read_emissions(path)
    rollbacks: list[Rollback] = []
    if not Path(path).exists():
        return emissions, ()
    for number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if raw.get("kind") != ROLLBACK:
                continue
            rollbacks.append(
                Rollback(
                    seq=int(raw["seq"]),
                    at=float(raw["at"]),
                    cut_seq=int(raw["cut_seq"]),
                    repo=str(raw["repo"]),
                    undone=tuple(int(u) for u in raw.get("undone") or ()),
                    closed={canonical_key(str(k)): int(v)
                            for k, v in (raw.get("closed") or {}).items()},
                    failed=tuple(raw.get("failed") or ()),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise EmissionError(f"{path}:{number} is not a rollback: {exc}") from exc
    return emissions, tuple(rollbacks)


def read_emissions(path: Path) -> tuple[Emission, ...]:
    if not Path(path).exists():
        return ()
    out: list[Emission] = []
    for number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if raw.get("kind") == ROLLBACK:
                continue
            out.append(
                Emission(
                    seq=int(raw["seq"]),
                    at=float(raw["at"]),
                    cut_seq=int(raw["cut_seq"]),
                    repo=str(raw["repo"]),
                    # Re-spelled on read. The log is keyed by unit key, and
                    # an emission recorded before the separator changed holds
                    # the old spelling -- which `standing()` would fail to
                    # match against a freshly computed key, conclude nothing
                    # had been emitted, and create every issue a second time.
                    # That is the exact duplication this log exists to stop.
                    issues={canonical_key(k): v
                            for k, v in (raw.get("issues") or {}).items()},
                    wired=tuple(_canonical_pair(p)
                                for p in (raw.get("wired") or ())),
                    lineage=raw.get("keys") == LINEAGE,
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise EmissionError(f"{path}:{number} is not an emission: {exc}") from exc
    return tuple(out)


def keyed_by_lineage(path: Path) -> bool:
    """Whether a project's unit keys are lineage roots (M13, D1).

    Read off its own emission record, never configured: true for a project
    nothing has been emitted for yet, and for one whose every record says so.
    One record without the marker -- DARK's whole log -- keeps the project on
    the live-anchor keys its issues were created under, because re-keying
    them would rename every unit an issue already carries."""
    return all(e.lineage for e in read_emissions(path))


def _append(path: Path, record) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")


def _next_seq(path: Path) -> int:
    """One sequence across both kinds: they share a file, and two counters
    over one append-only log is how a duplicate seq happens."""
    emissions, rollbacks = read_log(path)
    return max([e.seq for e in emissions] + [r.seq for r in rollbacks] + [0]) + 1


def _pair(blocked: str, blocker: str) -> str:
    return f"{blocked}<-{blocker}"


def _canonical_pair(pair: str) -> str:
    """A wired edge re-spelled on both sides, for the reason above."""
    blocked, _, blocker = pair.partition("<-")
    return _pair(canonical_key(blocked), canonical_key(blocker))


class RunBusy(RuntimeError):
    """An emission is already in flight for this project.

    Refused rather than queued or interleaved. Two concurrent runs would each
    read the emission log before the other had written it, conclude that
    nothing had been emitted, and both create every issue -- which is exactly
    the duplication the log exists to prevent.
    """


class Run:
    """One emission, watchable while it happens.

    Progress is the whole reason this exists: a `dark`-sized run is 358 paced
    writes, about six minutes, and a caller needs to see how many tickets have
    been processed rather than whether the request has returned.

    Every read and write is under the lock. The worker thread writes and an
    HTTP request reads, so a status assembled field by field could otherwise
    report a `processed` from after a `total` was replaced.
    """

    def __init__(self, project: str, now_fn: Callable[[], float]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.project = project
        self._lock = threading.Lock()
        self._state: dict = {
            "run_id": self.id,
            "project": project,
            # Two different things, and they shared one field once. `phase` is
            # the run's lifecycle -- running, done, failed. `step` is what the
            # work is doing -- creating issues, then wiring order. Conflated,
            # the first progress report set phase to "creating", every later
            # one failed the "still running?" guard, and the count froze at one
            # while the run went on for six minutes. Found by driving it; no
            # unit test sent a second report.
            "phase": "running",
            "step": None,
            "total": None,
            "processed": 0,
            "created": 0,
            "skipped": 0,
            "failed": 0,
            "wired": 0,
            "unwired": 0,
            "edited": 0,
            "withdrawn": 0,
            "touched": 0,
            "started_at": now_fn(),
            "finished_at": None,
            "error": None,
            "result": None,
        }

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state["phase"] == "running"

    def report(self, progress: dict) -> None:
        """Called from the worker after each unit and each edge."""
        with self._lock:
            if self._state["phase"] != "running":
                return
            # Never `phase`: that is this object's business, not the
            # worker's.
            for field in (
                # `closed` is a rollback's; the rest are an emission's. One
                # list because one registry runs both, and a field missing
                # here is a counter that silently stays zero on the page
                # while the work it counts is happening.
                "step", "total", "processed", "created", "skipped",
                "failed", "wired", "unwired", "closed", "edited",
                "withdrawn", "touched", "to_wire", "adopted", "swept",
            ):
                if field in progress:
                    self._state[field] = progress[field]

    def finish(self, result: dict | None, error: str | None, at: float) -> None:
        with self._lock:
            # A refusal -- no repo, no cut, drift -- is a FINISHED run, not a
            # failed one. Painting an error on the dashboard for a project that
            # is merely not ready would teach a reader to ignore the colour.
            self._state["phase"] = "failed" if error else "done"
            self._state["error"] = error
            self._state["result"] = result
            self._state["finished_at"] = at
            if result and isinstance(result.get("counts"), dict):
                for field, value in result["counts"].items():
                    if field in self._state:
                        self._state[field] = value

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)


class Runs:
    """The emissions in flight in this process, one at a time per project.

    In-process and deliberately not persisted: a run is an activity, and the
    durable record of what an activity achieved is the emission log. After a
    restart there is no run to report and the log is the answer -- which is why
    a status of `None` means "nothing running", never "nothing happened".

    `spawn` is injected so the test suite stays deterministic and thread-free:
    passing `lambda fn: fn()` makes the "background" work finish before `start`
    returns, which is what a test wants and never what production wants.
    """

    def __init__(
        self,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._spawn = spawn or self._thread
        self._now = now_fn
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}

    @staticmethod
    def _thread(work: Callable[[], None]) -> None:
        # Daemon: a half-finished emission must not keep the process alive at
        # shutdown. What it managed to create is already in the log.
        threading.Thread(target=work, daemon=True).start()

    def start(
        self,
        project: str,
        work: Callable[[Callable[[dict], None]], dict],
        on_finish: Callable[[dict], None] | None = None,
    ) -> Run:
        """Begin an emission. `work` is handed a `report` callback.

        `on_finish` gets the run's final snapshot once, whichever way the work
        ended -- the boundary where a durable record of the run is written,
        since this registry itself is forgotten on restart. Its own failure is
        swallowed: the run has already finished.

        Raises `RunBusy` if one is already in flight for this project.
        """
        with self._lock:
            existing = self._runs.get(project)
            if existing is not None and existing.active:
                raise RunBusy(
                    f"an emission for {project!r} is already running "
                    f"(run {existing.id})"
                )
            run = Run(project, self._now)
            self._runs[project] = run

        def body() -> None:
            try:
                result = work(run.report)
            except BaseException as exc:  # noqa: BLE001
                # A worker that dies silently leaves the run "running" forever
                # and the dashboard showing a spinner nobody can clear. Every
                # exception becomes a terminal state with its reason.
                run.finish(None, f"{type(exc).__name__}: {exc}", self._now())
            else:
                run.finish(result, None, self._now())
            if on_finish is not None:
                try:
                    on_finish(run.snapshot())
                except Exception:  # noqa: BLE001 -- degrade, never raise
                    pass

        self._spawn(body)
        return run

    def status(self, project: str) -> dict | None:
        with self._lock:
            run = self._runs.get(project)
        return run.snapshot() if run else None


def standing(log_path: Path, repo: str) -> tuple[dict[str, dict], set[str]]:
    """What is live on a repository: unit key -> issue, and the edges wired.

    The one place this is computed. It was two places for exactly as long as
    it took to add rollbacks: `emit` learned to subtract a withdrawal and the
    pre-flight count did not, so the panel said "0 new issues" while pressing
    the button would have created 144.

    **Per repository, across every cut** (docs/TICKETS.md §1). It used to be
    scoped to `(cut_seq, repo)`, so every new cut looked never-emitted and a
    Ship created every issue a second time -- which is how `dark` reached 288
    issues for 144 units. A cut is a version of a ticket's content, never a
    new set of tickets.

    Withdrawals are subtracted by issue NUMBER, never by unit key: a unit
    emitted, withdrawn and emitted again holds a live issue under a key that
    also appears in a rollback, and dropping it by name would create a third
    issue for work that already has one.

    The edges are the latest record's, because every record carries the full
    set: an edge the cut dropped is removed from the tracker and from the
    next record, and a union across records would resurrect it.

    **One key per issue.** A record that holds an issue under a new key makes
    an older line holding it under another key history: read naively, a later
    press would see the old key gone from the cut and close the issue the new
    key owns. DARK's log moved keys this way while units were keyed by their
    live anchor; a lineage key (M13) never moves, and every DARK rename is
    already recorded under its new key, so there is nothing left to alias.
    """
    emissions, rollbacks = read_log(log_path)
    mine = [e for e in emissions if e.repo == repo]
    closed = {
        n for record in rollbacks if record.repo == repo
        for n in record.closed.values()
    }
    known: dict[str, dict] = {}
    holder: dict[int, str] = {}
    for emission in mine:
        for key, issue in emission.issues.items():
            number = issue.get("number")
            if number is not None:
                previous = holder.get(int(number))
                if previous is not None and previous != key:
                    known.pop(previous, None)
                holder[int(number)] = key
            known[key] = issue
    known = {
        key: issue for key, issue in known.items()
        if issue.get("number") is None or int(issue["number"]) not in closed
    }
    wired = {pair for pair in (mine[-1].wired if mine else ())
             if all(k in known for k in pair.split("<-"))}
    return known, wired


# An identifier in a contract renumbers when its entry is superseded; that is a
# rename, not a change to what is asked for.
# Built from the shared constants, and matching exactly what the hand-written
# `\b[IBAST][·\-]\d+(?:-T)?\b` did: a different mask changes every stored
# fingerprint, and every closed issue would come back as a follow-up.
_IDENT = re.compile(
    rf"\b[{''.join(ALL_LAYERS)}]{SEPARATOR_CLASS}\d+(?:{re.escape(TEST_SUFFIX)})?\b"
)


# The section the renderer writes straight after the contract. The contract
# runs up to it, not to the next `## `: a spec body may carry headings of its
# own, and ending there left most of such an entry outside the fingerprint.
_AFTER_CONTRACT = "\n## Files you may write"
_V2 = "2:"
# V3 reads the contract without its provenance lines (M27), which tickets no
# longer carry. A V2 record still has them in its hash, so it is compared
# through the closed issue's own text, like V1 -- never as a changed contract.
_V3 = "3:"
# V4 adds the write set (M26): a unit closed `completed` that now owns a file
# it did not own when it merged has a contract nothing merged meets. Paths are
# sorted, so a reordered module map is not a change. Every older record is
# still compared as V3 -- DARK's hold V1, V2 or none, read through the closed
# issue's own text -- so no closed issue moves because the fingerprint grew.
_V4 = "4:"
# V5 (M25) reads the contract's entries as a set: a unit lists its scenarios in
# id order, so a renumbered scenario moved to the end and V4 saw a change in an
# unchanged contract (DARK S-260-T, S-263-T, S-315-T). A V4 record is compared
# through the closed issue's own text, so nothing settled moves because of it.
_V5 = "5:"
_WRITE_PATH = re.compile(r"^- `([^`]+)`$", re.M)


# The renderer's rewrite of a retired citation, `S-317 (was S-128)`. Read as
# the one identifier it stands for, so a rewrite is never a contract change.
_WAS = re.compile(rf"({_IDENT.pattern}) \(was {_IDENT.pattern}\)")


def _digest(section: str) -> str:
    section = _WAS.sub(r"\1", section)
    return hashlib.sha256(" ".join(_IDENT.sub("#", section).split()).encode()).hexdigest()


def contract_of(body: str) -> str:
    """Fingerprint of what a ticket asks to be built: its `## Contract`
    section, provenance lines out, identifiers masked, whitespace collapsed.
    Context -- blockers, the read set, the other contracts a file serves,
    labels -- is not in it, so a closed issue whose context moved stays
    settled and one whose contract moved gets a follow-up. The write set is
    in it (V4): a file the unit did not own when it merged is unmet work.
    Its entries are a set (V5): a renumber that reorders them is no change."""
    return _V5 + hashlib.sha256(
        "\n".join(_entry_digests(body) + _write_paths(body)).encode()).hexdigest()


def _write_paths(body: str) -> list[str]:
    text = (body or "").replace("\r\n", "\n")
    _, found, rest = text.partition(_AFTER_CONTRACT)
    return sorted(_WRITE_PATH.findall(rest.split("\n## ", 1)[0])) if found else []


def _entry_digests(body: str) -> list[str]:
    """The contract as what precedes its first entry, then each entry's
    masked digest, sorted."""
    text = (body or "").replace("\r\n", "\n")
    _, found, rest = text.partition("## Contract")
    section = strip_provenance(rest.split(_AFTER_CONTRACT, 1)[0]) if found else ""
    parts = _ENTRY_HEAD.split(section)
    return [_digest(parts[0])] + sorted(_digest(parts[i + 1]) for i in range(1, len(parts) - 1, 2))


def _contract_set(body: str) -> str:
    """The contract alone, entries as a set: how a record older than V4 is
    told a renumber from a change, through the closed issue's own text."""
    return hashlib.sha256("\n".join(_entry_digests(body)).encode()).hexdigest()


def _contract_v4(body: str) -> str:
    """The fingerprint records held before V5: entries in order."""
    return _V4 + hashlib.sha256(
        "\n".join([_contract_v3(body)] + _write_paths(body)).encode()).hexdigest()


def _contract_v3(body: str) -> str:
    """The contract alone, as records held it before the write set joined."""
    text = (body or "").replace("\r\n", "\n")
    _, found, rest = text.partition("## Contract")
    section = rest.split(_AFTER_CONTRACT, 1)[0] if found else ""
    return _V3 + _digest(strip_provenance(section))


def completed(live: dict[str, dict]) -> frozenset[str]:
    """The unit keys this unit's own record holds as closed `completed`: their
    files are on main. Read from the settled markers, never from the tracker."""
    return frozenset(key for key, issue in live.items()
                     if (issue.get("settled") or {}).get("state_reason") == "completed")


MERGE = "merge"


def merge_orders(edges: dict, issues: dict[str, dict], open_now) -> dict[str, tuple[str, ...]]:
    """Merge-order blockers (M31, D11): unit key -> keys that must merge first.

    Derived on every press from the emission record, never stored as an entry
    edge. One rule, read off DARK's two cases: a TEST follow-up of unit D and an
    IMPLEMENTATION follow-up of a unit D waits on, transitively, both open --
    the one opened later merges first. It is the repair. m1210-3: S-325's
    follow-up was out and the tests of units waiting on it were restated after
    it to pass on both sides of it, so S-325 waited for them. m1206-17: S-271-T's
    test follow-up was out and the storage fix it needed (S-217) came after, so
    the test waited for the fix. Which of the two was the repair is read off
    issue numbers, the one order the record holds for certain.

    `open_now(key, record)` says whether a recorded issue is still open."""
    follow = {k: int(v["number"]) for k, v in issues.items()
              if v.get("follow_up_of") and v.get("number") and open_now(k, v)}
    out: dict[str, list[str]] = {}
    for test in sorted(k for k in follow if is_test_key(k)):
        seen: set[str] = set()
        stack = [test[: -len(TEST_SUFFIX)]]
        while stack:
            for blocker in edges.get(stack.pop(), ()) or ():
                if blocker not in seen:
                    seen.add(blocker)
                    stack.append(blocker)
        for impl in sorted(k for k in seen if k in follow and not is_test_key(k)):
            blocked, first = (impl, test) if follow[test] > follow[impl] else (test, impl)
            out.setdefault(blocked, []).append(first)
    return {k: tuple(v) for k, v in out.items()}


def merge_cycle(edges: dict, merge: dict) -> "list[str] | None":
    """A loop through the projected edges and the merge orders, as a route
    `[a, b, ..., a]`, or None."""
    from mu_spec.slice_gates import _path, cycles
    joined = {k: tuple(edges.get(k, ()) or ()) + tuple(merge.get(k, ()))
              for k in set(edges) | set(merge)}
    found = cycles(joined)
    return _path(joined, found[0]).split(" -> ") if found else None


def prune_merge_cycles(edges: dict, merge: dict) -> "tuple[dict, list[dict]]":
    """The merge orders with every loop they close taken out (ratified
    2026-10-24): a loop is never a reason to refuse, so the orders inside it
    are dropped and reported, `{"loop": [a, ..., a], "orders": [{blocked,
    blocker}]}`, and the press goes on. Only orders whose two ends sit in the
    loop's group are dropped; the rest stand. A loop through projected edges
    alone is left in -- it cannot happen (DESIGN §4a), and `merge_cycle` on
    what comes back still finds it, for the caller to refuse."""
    from mu_spec.slice_gates import _path, cycles
    kept = {k: list(v) for k, v in merge.items()}
    dropped: list[dict] = []
    while True:
        joined = {k: tuple(edges.get(k, ()) or ()) + tuple(kept.get(k, ()))
                  for k in set(edges) | set(kept)}
        groups = [g for g in cycles(joined)
                  if any(b in g for k in g for b in kept.get(k, ()))]
        if not groups:
            break
        for group in groups:
            inside = set(group)
            orders = [{"blocked": k, "blocker": b} for k in sorted(inside)
                      for b in kept.get(k, ()) if b in inside]
            dropped.append({"loop": _path(joined, group).split(" -> "), "orders": orders})
            for o in orders:
                kept[o["blocked"]].remove(o["blocker"])
    return {k: tuple(v) for k, v in kept.items() if v}, dropped


def cycle_refusal(cycle: list[str]) -> dict:
    """The press refused: the projected edges alone loop. Gates make that
    impossible; this is the guard if one ever slips past them."""
    return {"emitted": False, "refused": [{"cycle": cycle}], "cycle": cycle,
            "reason": "the work units' own edges form a cycle, so nothing was "
                      f"written: {' -> '.join(cycle)}. Every unit in it waits on the "
                      "next. Fix the graph, take a cut, and press again."}


def _contract_v1(body: str) -> str:
    """The fingerprint records held before the contract ran to the next
    renderer section: up to the first `## ` of any kind."""
    text = (body or "").replace("\r\n", "\n")
    _, found, rest = text.partition("## Contract")
    return _digest(rest.split("\n## ", 1)[0] if found else "")


def _contract_moved(recorded: "str | None", issue_body: str, ticket_body: str) -> bool:
    """Whether the contract a closed issue was emitted with differs from the
    ticket as it renders now. A record from before the current fingerprint is
    compared through the issue's own text rather than its hash, so the change
    of fingerprint is not mistaken for a change of contract. Whatever the
    record, a contract whose entries are the same set as the closed issue's
    is not moved (M25): a renumber reorders them and asks for nothing."""
    if recorded and recorded.startswith(_V5):
        return recorded != contract_of(ticket_body)
    if recorded and recorded.startswith(_V4):
        if recorded == _contract_v4(ticket_body):
            return False
        return not issue_body or contract_of(issue_body) != contract_of(ticket_body)
    # Everything older compares as it did before V4: the write set it never
    # recorded must not read as a change.
    if recorded and recorded.startswith(_V3):
        moved = recorded != _contract_v3(ticket_body)
    elif issue_body:
        moved = _contract_v3(issue_body) != _contract_v3(ticket_body)
    elif recorded and recorded.startswith(_V2):
        text = (ticket_body or "").replace("\r\n", "\n").partition("## Contract")[2]
        moved = recorded != _V2 + _digest(text.split(_AFTER_CONTRACT, 1)[0])
    else:
        moved = recorded != _contract_v1(ticket_body)
    return moved and not (issue_body and _contract_set(issue_body) == _contract_set(ticket_body))


def _follow_up(ticket, of: int, delta: str = "", retest: bool = False) -> "tuple[str, str]":
    """Title and body of a follow-up: the ticket as rendered, pointing back,
    with what changed since `of` between the two. `retest`: what changed is
    the tests that judge it, not its own contract (T-623)."""
    what = "the tests that judge it" if retest else "this contract"
    return (f"{ticket.title} (follow-up to #{of})",
            f"> **Follow-up to #{of}.** That issue is closed and its work merged; {what} "
            f"changed after it. Build the difference against what #{of} "
            "merged -- everything below is the contract as it now stands. If what "
            f"#{of} merged already satisfies it, close this as already satisfied.\n\n"
            + (delta + "\n\n" if delta else "") + ticket.body)


# An entry's heading in a rendered `## Contract`, as render writes it.
_ENTRY_HEAD = re.compile(rf"^### ([{''.join(ALL_LAYERS)}]{SEPARATOR_CLASS}\d+) — ", re.M)


def _entries_of(body: str) -> dict[str, str]:
    """A rendered contract as entry id -> its text, title line onward."""
    text = (body or "").replace("\r\n", "\n")
    _, found, rest = text.partition("## Contract")
    parts = _ENTRY_HEAD.split(rest.split(_AFTER_CONTRACT, 1)[0]) if found else []
    return {parts[i].replace(LEGACY_SEPARATOR, SEPARATOR): parts[i + 1].strip()
            for i in range(1, len(parts) - 1, 2)}


def _delta(old_body: str, ticket_body: str, entries, of: int, diffs: bool = True) -> str:
    """`## Changed since #of`: each entry of the contract added, removed,
    renumbered or changed since the issue it follows, with the changed text
    as a diff -- so nobody re-derives it by diffing issue bodies (DARK
    S-286-T, S-309-T). Renumbering is read from each entry's `was`; text is
    compared as the contract fingerprint compares it, identifiers masked.
    Empty when the old contract cannot be read: no section beats a wrong one."""
    old, new = _entries_of(old_body), _entries_of(ticket_body)
    if not old:
        return ""
    was = {str(e.get("id")): [str(w) for w in e.get("was") or ()] for e in entries or ()}
    lines: list[str] = []
    claimed: set[str] = set()
    for ident, text in new.items():
        before = ident if ident in old else next(
            (w for w in was.get(ident, ()) if w in old), None)
        if before is None:
            lines.append(f"- `{ident}` — added")
            continue
        claimed.add(before)
        if _digest(old[before]) == _digest(text):
            if before != ident:
                lines.append(f"- `{ident}` — renumbered from `{before}`, text unchanged")
            continue
        lines.append(f"- `{ident}` — changed" + (f" (was `{before}`)" if before != ident else ""))
        if diffs:
            diff = list(difflib.unified_diff(old[before].splitlines(), text.splitlines(),
                                             lineterm="", n=1))[2:]
            lines.append("````diff\n" + "\n".join(diff) + "\n````")
    lines += [f"- `{ident}` — removed" for ident in old if ident not in claimed and ident not in new]
    return (f"## Changed since #{of}\n\n" + ("\n".join(lines) if lines else _NO_CHANGE))


_NO_CHANGE = "No entry was added, removed or changed; only identifiers or layout moved."


def _retested(of: int, test_key: str, number) -> str:
    """Above an implementation follow-up opened because its tests moved (T-623)."""
    return (f"## Tests changed since #{of}\n\n`{test_key}` was restated after #{of} "
            f"merged: its follow-up #{number} redefines the scenarios that judge this "
            "unit, while this unit's own contract stayed the same. Make the code pass "
            "them, or close this as already satisfied.")


_FOLLOW_TITLE = re.compile(r"\(follow-up to #(\d+)\)\s*$")


def _machine_block(body: str) -> dict:
    try:
        block = json.loads((body or "").split("```json")[-1].split("```")[0])
    except (ValueError, IndexError):
        return {}
    return block if isinstance(block, dict) else {}


def _open_by_key(client, repo: str, project: "str | None") -> "dict[str, list[dict]] | None":
    """This project's open issues on the tracker, by unit key, oldest first.
    None when they cannot be listed -- then nothing is adopted or swept,
    because absence of an answer is not an empty repository."""
    if not project or not hasattr(client, "open_issues"):
        return None
    try:
        listed = client.open_issues(repo)
    except GitHubError:
        return None
    out: dict[str, list[dict]] = {}
    for issue in listed:
        if not any(str(l).startswith(LABEL_NAMESPACE + ":") for l in issue.get("labels") or ()):
            continue
        block = _machine_block(issue.get("body") or "")
        if block.get("project") != project or not block.get("key"):
            continue
        out.setdefault(canonical_key(str(block["key"])), []).append(issue)
    for found in out.values():
        found.sort(key=lambda i: int(i["number"]))
    return out


def _adopt(issue: dict, pickup) -> dict:
    """A record for an issue this unit made but did not record. Its
    fingerprint is unknown, so the press re-renders and edits it if untouched."""
    record = {"id": issue["id"], "number": issue["number"], "url": issue.get("url", ""),
              "fingerprint": "", "contract": contract_of(issue.get("body") or ""),
              "labels": [l for l in issue.get("labels") or () if l not in pickup]}
    follow = _FOLLOW_TITLE.search(issue.get("title") or "")
    if follow:
        record["follow_up_of"] = int(follow.group(1))
    return record


def _touched(client, repo: str, number, pickup) -> "tuple[str | None, dict]":
    """Why this issue is not ours to write (None if it is), and what was read.

    Touched = closed for any reason this unit did not record, or carrying a
    pickup label (docs/TICKETS.md §2). A read that fails counts as touched:
    for a write that can destroy somebody's state, not knowing is a reason to
    stop. A skipped issue can be written on a re-run; a clobbered one cannot
    be restored.
    """
    try:
        state = client.issue_state(repo, int(number))
    except (GitHubError, TypeError, ValueError, AttributeError) as exc:
        return f"could not read the issue's state, so it was left untouched: {exc}", {}
    if state.get("state") == "closed":
        return (f"closed as {state.get('state_reason') or 'closed'} by someone "
                "else -- left alone"), state
    held = [label for label in state.get("labels") or () if label in pickup]
    if held:
        return f"picked up ({', '.join(held)}) -- never edited after pickup", state
    return None, state


def _ours(label: str, extra: "tuple[str, ...] | list[str]") -> bool:
    """A label this unit puts on its issues. On an edit these are replaced and
    every other label is kept."""
    return label.startswith((f"{LABEL_NAMESPACE}:", "kind:", "slice:")) or label in extra


def rollback(
    *,
    log_path: Path,
    repo: str,
    cut_seq: int | None = None,
    client,
    now_fn: Callable[[], float],
    on_progress: Callable[[dict], None] | None = None,
    pickup: "tuple[str, ...] | list[str]" = (),
) -> dict:
    """Withdraw every live issue on this repository, from any cut.

    The sync run against an empty cut (docs/TICKETS.md §4). `cut_seq` is only
    recorded: identity is per repository, so withdrawing "one cut's" issues
    no longer names a set.

    Closed, never deleted. REST cannot delete an issue; closing is also the
    reversible half, and a closed issue leaves the default `is:open` list,
    which is the visible outcome somebody asking for a rollback wants.

    A touched issue is skipped and NOT recorded as withdrawn, so it is never
    recreated: work somebody picked up or finished is theirs.

    **The one thing this does NOT do is decide.** A person asked; the set was
    already recorded.
    """
    emissions, _done = read_log(log_path)
    mine = [e for e in emissions if e.repo == repo]
    targets, _wired = standing(log_path, repo)
    # Highest issue number first. An emission creates a test unit before the
    # implementation that follows it, so closing in reverse withdraws the
    # dependent before the thing it depends on and never leaves a live issue
    # blocked by a withdrawn one, however far the run gets.
    order = sorted(targets, key=lambda k: -int(targets[k].get("number") or 0))

    if not order:
        return {
            "rolled_back": False,
            "reason": f"nothing to withdraw: no issue on {repo} is still "
                      "recorded as open by this unit",
            "repo": repo,
            "cut_seq": cut_seq,
        }

    closed: dict[str, int] = {}
    failed: list[dict] = []
    skipped: list[dict] = []
    total = len(order)

    def tick(step: str) -> None:
        if on_progress:
            on_progress({
                "step": step,
                "total": total,
                "processed": len(closed) + len(failed) + len(skipped),
                "closed": len(closed),
                "failed": len(failed),
                "skipped": len(skipped),
            })

    tick("closing")
    for key in order:
        number = targets[key].get("number")
        why, _state = _touched(client, repo, number, pickup)
        if why is not None and not why.startswith(f"closed as {WITHDRAWN}"):
            skipped.append({"key": key, "number": number, "reason": why})
            tick("closing")
            continue
        try:
            client.close_issue(repo, int(number))
        except (GitHubError, TypeError, ValueError) as exc:
            # Left OUT of `closed`, so it stays live. An issue still open on
            # the tracker must never be recreated -- that turns one stray
            # issue into two.
            failed.append({"key": key, "number": number, "reason": str(exc)})
        else:
            closed[key] = int(number)
        tick("closing")

    if closed or failed or skipped:
        _append(log_path, Rollback(
            seq=_next_seq(log_path), at=now_fn(), cut_seq=int(cut_seq or 0),
            repo=repo, undone=tuple(sorted(e.seq for e in mine)),
            closed=closed, failed=tuple(failed),
        ))

    return {
        "rolled_back": True,
        "repo": repo,
        "cut_seq": cut_seq,
        "closed": [{"key": k, "number": n} for k, n in closed.items()],
        "failed": failed,
        "skipped": skipped,
        "counts": {
            "targeted": total,
            "closed": len(closed),
            "failed": len(failed),
            "skipped": len(skipped),
        },
    }


def shipped_horizon(order, edges: dict, live, uses: "dict | None" = None) -> set[str]:
    """The units with no issue a `shipped` press creates (docs/TICKETS.md §5):
    everything a live issue waits on, transitively -- the gaps under the
    frontier. Three kinds of unit count as waited on, and each brings what it
    waits on, never what waits on it: an implementation whose own tests are
    out (merged tests with no code are broken); a unit owning what a test unit
    that is out imports (M29, DARK S-229 / S-134: `uses`, test key -> owners);
    and a new leaf one step past the frontier -- nothing waits on it, and
    everything it waits on beyond its own test unit is out or comes in this
    press (M29, DARK S-331: the goal of a cut, held by two presses). With
    nothing out it creates nothing. One function, so the pane's held count is
    what the press holds."""
    if not live:
        return set()
    missing = [k for k in order if k not in live]
    mates = [k for k in missing if not is_test_key(k) and k + TEST_SUFFIX in live]
    mates += [u for t, owners in sorted((uses or {}).items()) if t in live
              for u in owners if u in missing and u not in mates]
    allowed, stack = set(mates), [k for k in order if k in live] + mates
    while stack:
        for blocker in edges.get(stack.pop(), ()) or ():
            if blocker not in allowed:
                allowed.add(blocker)
                stack.append(blocker)
    waited: dict[str, set[str]] = {}
    for key in order:
        for blocker in edges.get(key, ()) or ():
            waited.setdefault(blocker, set()).add(key)
    out = {k for k in missing if k in allowed}
    for leaf in missing:
        if leaf in waited or leaf in out:
            continue
        group = {leaf}
        mate = leaf + TEST_SUFFIX
        if not is_test_key(leaf) and mate in missing and waited.get(mate) == {leaf}:
            group.add(mate)
        if all(b in live or b in out or b in group
               for m in group for b in edges.get(m, ()) or ()):
            out |= group
    return out


def emit(
    *,
    log_path: Path,
    repo: str,
    cut_seq: int,
    order: list[str],
    edges: dict,
    fetch: Callable[[str], dict],
    client,
    now_fn: Callable[[], float],
    labels: "tuple[str, ...] | list[str]" = (),
    on_progress: Callable[[dict], None] | None = None,
    waves: "list[list[str]] | None" = None,
    horizon: "int | str | None" = None,
    pickup: "tuple[str, ...] | list[str]" = (),
    project: "str | None" = None,
    retired: "dict[str, str | None] | None" = None,
    lineage: bool = False,
    uses: "dict[str, tuple[str, ...]] | None" = None,
    only: "str | None" = None,
) -> dict:
    """Make the tracker say what the cut says (docs/TICKETS.md §3).

    `only` is the one-unit press (M30): that unit is created or edited, the
    orders touching it are wired, and nothing else is written -- no other
    edit, no withdrawal, no sweep. The probe before a full batch. `uses` is
    the `shipped` horizon's test unit -> owners of what it imports (M29).

    Per unit: unchanged -> nothing, and no call at all; changed and untouched
    -> edited in place; changed and touched -> reported; new -> created;
    gone from the cut and untouched -> closed `not_planned`; gone and touched
    -> reported. A CLOSED issue, changed or gone, is `settled`: never written
    and never a warning -- it is done with, whatever its text would now say.
    `touched` is left for open work somebody holds, the one case where
    releasing it means anything. Then every dependency is made to match the cut.

    `order` is the unit keys in dependency order; `edges` maps a unit to the
    units it follows; `waves` is that order grouped, for `horizon` -- the
    number of waves still holding an unshipped unit that this press creates
    issues for. `horizon="shipped"` is the other reading: create only the units
    a live issue waits on, transitively -- the gaps under what is already out,
    never past it, however a re-cut renumbered the waves -- so nobody has to
    remember how far the last press reached. The horizon limits
    creation only: an edit or a withdrawal is never held back. `pickup` is the labels a consumer adds on pickup; with
    none, nothing is edited or closed, because untouched cannot be told from
    admitted.

    Every write re-reads the issue first and reads it once more after. A
    pickup label that appeared in between is reported in `raced` -- never
    silent.

    `lineage` (the caller reads it from this log, `keyed_by_lineage`) means
    the keys are lineage roots, and the record says so. Then a follow-up is
    also attached as a sub-issue under its unit's ROOT issue -- the first one
    the key had -- so every id the unit lived through has its own issue,
    one tree per unit. A link that fails is recorded and retried by the next
    press; the follow-up is never created twice. A parent found open after
    the attach is reported in `raced` and left as it is.

    With `project`, the press first lists the repository's open issues once.
    A key with no recorded issue but an open one of ours ADOPTS it instead of
    creating a second -- a press that died before writing its record left
    issues nobody here knew about. At the end, an open issue of ours that no
    key owns and nobody picked up is closed `not_planned` (`swept`). The
    record is written even when the press dies part-way. Last, every order an
    open issue declares is checked against what the tracker holds: one waiting
    on a key no open or `completed` issue carries is reported in `dead`.
    """
    live, wired_before = standing(log_path, repo)
    # Read before adoption replaces any record: a file is on main once a unit
    # writing it was closed `completed`, as this record holds it.
    existing = completed(live)

    if not order:
        return {
            "emitted": False,
            "reason": "there are no work units in this cut to emit",
            "repo": repo,
            "cut_seq": cut_seq,
        }
    if only is not None and only not in order:
        return {
            "emitted": False,
            "reason": f"{only} is not a work unit of cut {cut_seq}, so there is "
                      "nothing to ship for it",
            "repo": repo,
            "cut_seq": cut_seq,
        }

    def in_scope(key: str) -> bool:
        """Whether this press may write for `key`: every unit, or the one."""
        return only is None or key == only

    # The sequence the emission record will carry, and so the batch every
    # ticket written in this press is stamped with.
    batch = _next_seq(log_path)
    in_cut = set(order)

    ours = _open_by_key(client, repo, project)
    adopted: list[dict] = []
    for key in order:
        if key in live or not (ours or {}).get(key) or not in_scope(key):
            continue
        first = ours[key][0]
        live[key] = _adopt(first, pickup)
        adopted.append({"key": key, "number": first["number"], "url": first.get("url", "")})

    # The units' own edges cannot loop (DESIGN §4a); if they ever do, nothing
    # is written. Merge order (M31) never refuses: the orders a loop runs
    # through are dropped and reported. Open means listed open; without a
    # listing, not recorded as settled.
    cycle = merge_cycle(edges, {})
    if cycle:
        return {**cycle_refusal(cycle), "repo": repo, "cut_seq": cut_seq}
    listed = {int(i["number"]) for found in (ours or {}).values() for i in found}

    def open_in(numbers: set[int]):
        return lambda _k, v: (int(v["number"]) in numbers if ours is not None
                              else not v.get("settled"))

    kept, dropped = prune_merge_cycles(edges, merge_orders(edges, live, open_in(listed)))
    orders = {"now": kept, "dropped": dropped}

    missing = [key for key in order if key not in live]
    held: list[str] = []
    if only is not None:
        held = [key for key in missing if key != only]
        missing = [key for key in missing if key == only]
    elif horizon == "shipped":
        allowed = shipped_horizon(order, edges, live, uses)
        held = [key for key in missing if key not in allowed]
        missing = [key for key in missing if key in allowed]
    elif horizon is not None and waves:
        chosen = [wave for wave in waves if any(k not in live for k in wave)]
        allowed = {k for wave in chosen[: max(0, int(horizon))] for k in wave}
        held = [key for key in missing if key not in allowed]
        missing = [key for key in missing if key in allowed]

    created: list[dict] = []
    edited: list[dict] = []
    withdrawn: list[dict] = []
    touched: list[dict] = []
    settled: list[dict] = []
    follow_ups: list[dict] = []
    swept: list[dict] = []
    raced: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    wired: list[dict] = []
    unwired: list[dict] = []
    removed: list[dict] = []
    dead: list[dict] = []
    linked: list[dict] = []
    unlinked: list[dict] = []
    # Issues GitHub created without labels this press sent (M30, m1109-5).
    unlabelled: list[dict] = []
    # The payload each key was rendered from, and every issue state read.
    asks: dict[str, dict] = {}
    seen: dict[int, dict] = {}
    open_labels = {int(i["number"]): list(i.get("labels") or ())
                   for found in (ours or {}).values() for i in found}
    open_numbers = set(open_labels)
    issues: dict[str, dict] = dict(live)
    # Whether a settled marker was set or cleared, so the record carries it.
    marks = {"changed": False}
    # Implementation key -> the number of its test unit's follow-up, when this
    # press followed it up because the tests moved after it merged (T-623).
    retests: dict[str, int] = {}
    # Issues whose dependencies are frozen: picked up, or unreadable.
    frozen: set[str] = set()
    work = [k for k in order if (k in live or k in missing) and in_scope(k)]
    # How many orders the wiring step will declare, once it is known. That
    # step is one GitHub call per edge and can outlast everything before it;
    # without a total the page sat at "109 of 109" for thirteen minutes.
    pace = {"to_wire": 0}

    def tick(step: str) -> None:
        """Per item, not per phase: the point is watching a run advance."""
        if on_progress is None:
            return
        on_progress({
            "step": step,
            "total": len(work),
            "processed": len(created) + len(edited) + len(skipped)
                         + len(failed) + len(touched) + len(settled)
                         + len(follow_ups)
                         # a follow-up adopted rather than created is a unit
                         # processed too; uncounted, the bar stopped short
                         + sum(1 for a in adopted if a.get("follow_up")),
            "created": len(created),
            "edited": len(edited),
            "withdrawn": len(withdrawn),
            "touched": len(touched),
            "skipped": len(skipped),
            "failed": len(failed),
            "wired": len(wired),
            "unwired": len(unwired),
            "to_wire": pace["to_wire"],
            "adopted": len(adopted),
            "swept": len(swept),
        })

    def fetched(key: str) -> dict:
        """The unit, with `follows_was` cut at any blocker that has a follow-up:
        that blocker's old key is closed `completed` for work the follow-up has
        not done, and a consumer reading the old key would count it done."""
        unit = fetch(key)
        if isinstance(unit, dict) and orders["now"].get(key):
            unit = {**unit, "merge_after": list(orders["now"][key])}
        asks[key] = unit
        was = unit.get("follows_was") if isinstance(unit, dict) else None
        if was:
            kept = {}
            for k, v in was.items():
                issue = issues.get(k, {})
                if not issue.get("follow_up_of"):
                    kept[k] = v
                    continue
                # A follow-up opened before the blocker was renamed answers
                # to the old key it was opened under: keep the chain up to it.
                opened = [l[len(LABEL_NAMESPACE) + 1:] for l in issue.get("labels") or ()
                          if l.startswith(LABEL_NAMESPACE + ":")]
                own = opened[0] + k[len(anchor_of(k)):] if len(opened) == 1 else None
                if own in v:
                    kept[k] = v[:v.index(own) + 1]
            unit = {**unit, "follows_was": kept}
            asks[key] = unit
        return unit

    def look(key: str) -> "tuple[str | None, dict]":
        if not pickup:
            return ("no pickup labels are configured, so an issue somebody "
                    "picked up cannot be told from one nobody has -- left alone"), {}
        why, state = _touched(client, repo, issues[key].get("number"), pickup)
        if state:
            seen[int(issues[key]["number"])] = state
        return why, state

    def still_settled(key: str, fingerprint: str) -> "str | None":
        """The `state_reason` a settled issue was closed with, if its marker
        still holds: same issue number, not in the open list, and the ticket
        says what it said when settled ("gone" for a unit out of the cut).
        Then the read is skipped -- re-reading 193 closed issues made an
        unmoved DARK press take five minutes. Keyed on the number, never the
        key alone. A marker on an issue reopened by hand is cleared."""
        mark = issues[key].get("settled")
        if not mark or ours is None:
            return None
        number = int(issues[key].get("number") or 0)
        if number in open_numbers:
            live[key] = issues[key] = {k: v for k, v in issues[key].items() if k != "settled"}
            marks["changed"] = True
            return None
        if int(mark.get("number") or 0) != number or mark.get("fingerprint") != fingerprint:
            return None
        return str(mark.get("state_reason") or "closed")

    def settle(key: str, fingerprint: str, state: dict) -> None:
        mark = {"number": int(issues[key]["number"]), "fingerprint": fingerprint,
                "state_reason": state.get("state_reason") or "", "key": key}
        if issues[key].get("settled") != mark:
            issues[key] = {**issues[key], "settled": mark}
            marks["changed"] = True

    def followed(ticket, key: str, of: int) -> "tuple[str, str]":
        """A follow-up with its delta against the merged issue `of` -- read
        once, and already read when the follow-up is first opened. Near the
        size ceiling the delta keeps its ids and drops the diffs."""
        state = seen.get(int(of))
        if state is None:
            try:
                state = seen[int(of)] = client.issue_state(repo, int(of))
            except (GitHubError, TypeError, ValueError, AttributeError):
                state = {}
        entries = (asks.get(key) or {}).get("entries")
        retest = retests.get(key) or (issues.get(key) or {}).get("retest")

        def head(diffs: bool) -> str:
            delta = _delta(state.get("body") or "", ticket.body, entries, of, diffs=diffs)
            if not retest:
                return delta
            # Opened because its tests moved (T-623): say so, and add the
            # delta only if its own contract moved too.
            note = _retested(of, key + TEST_SUFFIX, retest)
            return note + ("\n\n" + delta if delta and not delta.endswith(_NO_CHANGE) else "")

        title, body = _follow_up(ticket, of, head(True), retest=bool(retest))
        if len(body) > BODY_LIMIT:
            title, body = _follow_up(ticket, of, head(False), retest=bool(retest))
        return title, body

    def check_labels(key: str, issue, sent) -> None:
        """What GitHub kept against what was sent, from the create's own
        response: a token without push access drops every label silently."""
        kept = getattr(issue, "labels", None)
        if kept is None:
            return
        lost = [label for label in sent if label not in kept]
        if lost:
            unlabelled.append({"key": key, "number": issue.number, "missing": lost,
                               "reason": "GitHub created the issue without these labels -- "
                                         "does the token have push access?"})

    def verify(open_now: set[int]) -> None:
        """After the press, as a consumer would read it: every blocker an open,
        unpicked issue of ours names must be carried -- under that key or one
        `blocked_by_was` lists for it -- by an issue that is open or closed
        `completed`. Anything else waits forever, and on DARK it did (~30 units
        behind keys whose only issue was closed `not_planned`; S-273 behind a
        key no issue carried) while the press reported nothing."""
        for blocked in order:
            mine = issues.get(blocked)
            if (not mine or blocked in frozen or blocked not in asks
                    or int(mine.get("number") or 0) not in open_now
                    or any(l in pickup for l in open_labels.get(int(mine["number"]), ()))):
                continue
            was = asks[blocked].get("follows_was") or {}
            for blocker in (list(asks[blocked].get("follows") or ())
                            + list(asks[blocked].get("merge_after") or ())):
                names = {blocker, *was.get(blocker, ())}
                why = f"no issue carries {blocker}"
                for name in sorted(names):
                    number = int((issues.get(name) or {}).get("number") or 0)
                    if not number:
                        continue
                    if number in open_now:
                        why = None
                        break
                    state = seen.get(number)
                    if state is None:
                        try:
                            state = seen[number] = client.issue_state(repo, number)
                        except (GitHubError, AttributeError) as exc:
                            why = f"#{number} could not be read to check it: {exc}"
                            continue
                    carried = _machine_block(state.get("body") or "").get("key") or name
                    if state.get("state") == "open" or (
                            state.get("state_reason") == "completed"
                            and canonical_key(str(carried)) in names):
                        why = None
                        break
                    why = (f"#{number} is closed {state.get('state_reason') or 'closed'}"
                           if state.get("state_reason") != "completed" else
                           f"#{number} was closed completed as {carried}, a key "
                           f"{blocked} does not name for it")
                if why:
                    dead.append({"blocked": blocked, "number": mine["number"],
                                 "blocker": blocker, "reason": why})

    def after(key: str, what: str) -> None:
        """The second read. The consumer admits by labelling first and reading
        second, so a label that appeared since our first read means the write
        may have landed on an issue it had just taken."""
        try:
            state = client.issue_state(repo, int(issues[key]["number"]))
        except (GitHubError, TypeError, ValueError, AttributeError):
            return
        if any(label in pickup for label in state.get("labels") or ()):
            raced.append({"key": key, "number": issues[key]["number"],
                          "reason": f"{what} while it was being picked up"})

    def write(key: str, ticket, state: dict) -> None:
        """Edit an untouched open issue in place to say what `ticket` says."""
        mine = issues[key]
        try:
            # Labels by difference, never by replacement: a PATCH of the whole
            # set would erase a pickup label added since our read, and the
            # issue would look untouched to every Ship after.
            number = int(mine["number"])
            of = mine.get("follow_up_of")
            title, body = followed(ticket, key, of) if of else (ticket.title, ticket.body)
            client.update_issue(repo, number, title, body)
            current = list(state.get("labels") or ())
            add = [l for l in ticket.labels if l not in current]
            if add:
                client.add_labels(repo, number, add)
            wrote = tuple(mine.get("labels") or ()) + tuple(labels)
            for label in current:
                if _ours(label, wrote) and label not in ticket.labels:
                    client.remove_label(repo, number, label)
        except (GitHubError, TypeError, ValueError, AttributeError) as exc:
            failed.append({"key": key, "reason": f"could not be edited: {exc}"})
            frozen.add(key)
            return
        after(key, "edited")
        issues[key] = {**mine, "fingerprint": ticket.fingerprint,
                       "contract": contract_of(ticket.body),
                       "batch": batch, "labels": list(ticket.labels)}
        if not any(e["key"] == key for e in edited):
            edited.append({"key": key, "number": mine["number"], "url": mine.get("url", "")})

    closed: dict[str, int] = {}
    now_wired = {p for p in wired_before if all(k in issues for k in p.split("<-"))}
    try:
        # -- units that left the cut: withdraw, highest number first --------------
        gone = sorted((k for k in live if k not in in_cut and only is None),
                      key=lambda k: -int(live[k].get("number") or 0))
        for key in gone:
            reason = still_settled(key, "gone")
            if reason is not None:
                settled.append({"key": key, "number": live[key].get("number"),
                                "reason": f"gone from the cut, not closed: closed as "
                                          f"{reason} by someone else -- left alone"})
                frozen.add(key)
                tick("withdrawing")
                continue
            why, _state = look(key)
            if why is not None:
                (settled if _state.get("state") == "closed" else touched).append(
                    {"key": key, "number": live[key].get("number"),
                     "reason": f"gone from the cut, not closed: {why}"})
                if _state.get("state") == "closed":
                    settle(key, "gone", _state)
                frozen.add(key)
                tick("withdrawing")
                continue
            try:
                client.close_issue(repo, int(live[key]["number"]))
            except (GitHubError, TypeError, ValueError) as exc:
                failed.append({"key": key, "reason": f"could not be withdrawn: {exc}"})
                frozen.add(key)
            else:
                after(key, "withdrawn")
                closed[key] = int(live[key]["number"])
                withdrawn.append({"key": key, "number": closed[key]})
                issues.pop(key, None)
            tick("withdrawing")

        # -- units already out: compare, edit what changed ------------------------
        for key in order:
            if key not in live or not in_scope(key):
                continue
            try:
                ticket = render(fetched(key), repo=repo, cut_seq=cut_seq,
                                extra_labels=labels, batch=batch, existing=existing,
                                retired=retired)
            except (ValueError, KeyError) as exc:
                failed.append({"key": key, "reason": f"could not be rendered: {exc}"})
                frozen.add(key)
                tick("editing")
                continue
            # T-623: this press opened a follow-up for the unit's tests, so if
            # the implementation is merged its code is judged by tests it was
            # never built against. A test unit precedes its implementation in
            # `order`, so the follow-up is already known here.
            # ponytail: in-press signal only; a press that dies between the two
            # follow-ups loses the second. Persist a marker if that bites.
            retest = next((f["number"] for f in follow_ups
                           if not is_test_key(key) and f["key"] == key + TEST_SUFFIX), None)
            unchanged = ticket.fingerprint == live[key].get("fingerprint")
            if unchanged and retest is None:
                skipped.append({"key": key, "number": live[key].get("number"),
                                "url": live[key].get("url", ""),
                                "reason": f"already emitted as #{live[key].get('number')}, "
                                          "unchanged"})
                tick("editing")
                continue
            if ticket.oversized:
                failed.append({"key": key, "reason": f"the rendered body is too large "
                               f"for the tracker ({len(ticket.body)} characters)"})
                frozen.add(key)
                tick("editing")
                continue
            reason = still_settled(key, ticket.fingerprint)
            if reason is not None and not (retest and reason == "completed"):
                settled.append({"key": key, "number": live[key].get("number"),
                                "reason": f"context changed, contract did not: closed as "
                                          f"{reason} by someone else -- left alone"})
                frozen.add(key)
                tick("editing")
                continue
            why, state = look(key)
            if why is not None and state.get("state") == "closed":
                retested = retest is not None and state.get("state_reason") == "completed"
                if retested:
                    retests[key] = retest
                elif not _contract_moved(live[key].get("contract"), state.get("body") or "",
                                         ticket.body):
                    settled.append({"key": key, "number": live[key].get("number"),
                                    "reason": f"context changed, contract did not: {why}"})
                    settle(key, ticket.fingerprint, state)
                    frozen.add(key)
                    tick("editing")
                    continue
                # The contract moved after its work merged. A fresh open issue is
                # the only shape that gets the difference built.
                of = int(live[key]["number"])
                # Lineage: the follow-up hangs under the unit's first issue, the
                # one every later follow-up hangs under too.
                tree = ({"root": live[key].get("root") or {"id": live[key].get("id"), "number": of},
                         "linked": False} if lineage else {})
                found = [i for i in (ours or {}).get(key, ())
                         if int(i["number"]) not in (of, int((tree.get("root") or {}).get("number") or 0))]
                if found:
                    issues[key] = {**_adopt(found[0], pickup), "follow_up_of": of, **tree}
                    adopted.append({"key": key, "number": found[0]["number"],
                                    "url": found[0].get("url", ""), "follow_up": True})
                    tick("editing")
                    continue
                title, body = followed(ticket, key, of)
                if len(body) > BODY_LIMIT:
                    failed.append({"key": key, "reason": f"follow-up to #{of} is too large "
                                   f"for the tracker ({len(body)} characters)"})
                    frozen.add(key)
                    tick("editing")
                    continue
                try:
                    issue = client.create_issue(repo, title, body, ticket.labels)
                except GitHubError as exc:
                    failed.append({"key": key, "reason": f"follow-up to #{of} not created: {exc}"})
                    frozen.add(key)
                    tick("editing")
                    continue
                check_labels(key, issue, ticket.labels)
                issues[key] = {"id": issue.id, "number": issue.number, "url": issue.url,
                               "fingerprint": ticket.fingerprint, "contract": contract_of(ticket.body),
                               "batch": batch, "labels": list(ticket.labels), "follow_up_of": of,
                               **({"retest": retests[key]} if key in retests else {}), **tree}
                follow_ups.append({"key": key, "title": title, "number": issue.number,
                                   "url": issue.url, "follow_up_of": of,
                                   "labels": list(ticket.labels),
                                   **({"retest": retests[key]} if key in retests else {})})
                tick("editing")
                continue
            if unchanged:
                # Only its tests moved, and it is not merged: nothing to write.
                skipped.append({"key": key, "number": live[key].get("number"),
                                "url": live[key].get("url", ""),
                                "reason": f"already emitted as #{live[key].get('number')}, "
                                          "unchanged"})
                tick("editing")
                continue
            if why is not None:
                # Its old fingerprint is kept, so the change stays reported until
                # somebody releases the issue.
                touched.append({"key": key, "number": live[key].get("number"),
                                "reason": f"changed, not edited: {why}"})
                frozen.add(key)
                tick("editing")
                continue
            write(key, ticket, state)
            tick("editing")

        # -- new units, within the horizon ----------------------------------------
        for key in missing:
            try:
                ticket = render(fetched(key), repo=repo, cut_seq=cut_seq,
                                extra_labels=labels, batch=batch, existing=existing,
                                retired=retired)
            except (ValueError, KeyError) as exc:
                # An unsound graph refuses one unit at a time. One bad unit must
                # not cost the other hundred and forty-three.
                failed.append({"key": key, "reason": f"could not be rendered: {exc}"})
                tick("creating")
                continue
            if ticket.oversized:
                skipped.append({"key": key, "reason": f"the rendered body is too large "
                                f"for the tracker ({len(ticket.body)} characters)"})
                tick("creating")
                continue
            try:
                issue = client.create_issue(repo, ticket.title, ticket.body, ticket.labels)
            except GitHubError as exc:
                failed.append({"key": key, "reason": str(exc)})
                tick("creating")
                continue
            check_labels(key, issue, ticket.labels)
            # The labels written are recorded so a later edit can remove the ones
            # this unit put there -- a project label renamed since is still ours.
            issues[key] = {"id": issue.id, "number": issue.number, "url": issue.url,
                           "fingerprint": ticket.fingerprint, "contract": contract_of(ticket.body),
                           "batch": batch, "labels": list(ticket.labels)}
            created.append({"key": key, "title": ticket.title, "number": issue.number,
                            "url": issue.url, "labels": list(ticket.labels)})
            tick("creating")

        # -- lineage: each follow-up under its unit's root issue -------------------
        # Any follow-up not yet attached, this press's or one a failed link left.
        # Not held back by `frozen`: a link changes nothing a consumer reads, and
        # skipping a closed follow-up would lose its place in the tree for good.
        for key in order if lineage else ():
            mine = issues.get(key) or {}
            root = mine.get("root")
            if not root or mine.get("linked") or not in_scope(key):
                continue
            parent = int(root.get("number") or 0)
            try:
                client.add_sub_issue(repo, parent, int(mine["id"]))
            except (GitHubError, TypeError, ValueError, AttributeError) as exc:
                if not (isinstance(exc, GitHubError) and exc.status == 422
                        and _ALREADY_SUB.search(str(exc))):
                    unlinked.append({"key": key, "number": mine.get("number"), "parent": parent,
                                     "reason": f"not attached under #{parent}, retried next "
                                               f"press: {exc}"})
                    continue
            else:
                # TODO(M13): unverified whether attaching under a CLOSED parent
                # reopens it. Read it back; if it is open, say so and close
                # nothing -- a reopen this unit did not record is not its own.
                try:
                    after_attach = client.issue_state(repo, parent)
                except (GitHubError, AttributeError):
                    after_attach = {}
                if after_attach.get("state") == "open":
                    raced.append({"key": key, "number": parent, "reason":
                                  f"open after #{mine.get('number')} was attached as its "
                                  "sub-issue -- reopened by the attach or by someone "
                                  "else; left as it is"})
            issues[key] = {**mine, "linked": True}
            marks["changed"] = True
            linked.append({"key": key, "number": mine.get("number"), "parent": parent})

        # -- merge order, again, as this press left the tracker ------------------
        # A follow-up opened above changes what merges first, and the unit it
        # orders was rendered before it existed (m1210-3: the restated tests
        # came after S-325's follow-up). Re-rendered and edited now, so the
        # press that opens a repair is the one that says what it precedes.
        opened = {int(i["number"]) for i in created + follow_ups}
        open_after = (listed | opened) - set(closed.values())
        post, orders["dropped"] = prune_merge_cycles(
            edges, merge_orders(edges, issues, open_in(open_after)))
        if post != orders["now"]:
            before, orders["now"] = orders["now"], post
            for key in order:
                mine = issues.get(key) or {}
                if (post.get(key) == before.get(key) or key in frozen or not in_scope(key)
                        or int(mine.get("number") or 0) not in open_after):
                    continue
                try:
                    ticket = render(fetched(key), repo=repo, cut_seq=cut_seq,
                                    extra_labels=labels, batch=batch, existing=existing,
                                    retired=retired)
                except (ValueError, KeyError):
                    continue
                if ticket.fingerprint == mine.get("fingerprint") or ticket.oversized:
                    continue
                why, state = look(key)
                if why is not None:
                    touched.append({"key": key, "number": mine.get("number"),
                                    "reason": f"merge order changed, not edited: {why}"})
                    frozen.add(key)
                    continue
                write(key, ticket, state)
        merging = orders["now"]
        wiring = {k: tuple(edges.get(k, ()) or ()) + tuple(
                      b for b in merging.get(k, ()) if b not in (edges.get(k, ()) or ()))
                  for k in set(edges) | set(merging)}

        # -- the order: declare what the cut has, remove what it dropped ---------
        wanted = {
            _pair(blocked, blocker)
            for blocked in order if blocked in issues
            for blocker in wiring.get(blocked, ()) or ()
        }
        # A follow-up is a new issue under an old key: every order touching that
        # key is declared again, against the new number.
        renewed = {f["key"] for f in follow_ups}
        now_wired = {p for p in wired_before
                     if all(k in issues for k in p.split("<-"))
                     and not renewed.intersection(p.split("<-"))}
        def touches(blocked: str, blocker: str) -> bool:
            return only is None or only in (blocked, blocker)

        pace["to_wire"] = sum(
            1 for blocked in order if blocked in issues and blocked not in frozen
            for blocker in wiring.get(blocked, ()) or ()
            if _pair(blocked, blocker) not in now_wired and touches(blocked, blocker))
        tick("wiring")
        # The order as GitHub holds it, for every open issue nobody picked up:
        # the record keeps pairs of keys, not which issue a link points at, so
        # a key that moved issue -- a swept follow-up -- left links the pairs
        # call wired. Read, then add what is missing and remove links to our
        # own issues that no longer belong. Links to anyone else's are kept.
        synced: set[str] = set()
        if ours is not None and hasattr(client, "blocked_by"):
            for blocked in order:
                if (blocked not in issues or blocked in frozen or not in_scope(blocked)
                        or int(issues[blocked].get("number") or 0) not in open_numbers
                        or any(l in pickup for l in open_labels.get(int(issues[blocked]["number"]), ()))):
                    continue
                try:
                    native = client.blocked_by(repo, int(issues[blocked]["number"]))
                except GitHubError:
                    continue
                synced.add(blocked)
                want = {int(issues[b]["id"]) for b in wiring.get(blocked, ()) or () if b in issues}
                have = {int(x["id"]) for x in native}
                for link in native:
                    if int(link["id"]) in want or not any(
                            str(l).startswith(LABEL_NAMESPACE + ":") for l in link.get("labels") or ()):
                        continue
                    try:
                        client.remove_blocked_by(repo, int(issues[blocked]["number"]), int(link["id"]))
                    except GitHubError as exc:
                        unwired.append({"blocked": blocked, "blocker": f"#{link.get('number')}",
                                        "reason": f"a stale order could not be removed: {exc}"})
                        continue
                    removed.append({"blocked": blocked, "blocker": f"#{link.get('number')}"})
                now_wired = {p for p in now_wired if p.partition("<-")[0] != blocked}
                now_wired.update(_pair(blocked, b) for b in wiring.get(blocked, ()) or ()
                                 if b in issues and int(issues[b]["id"]) in have)
        for blocked in order:
            if blocked not in issues or blocked in frozen:
                continue
            for blocker in wiring.get(blocked, ()) or ():
                pair = _pair(blocked, blocker)
                if pair in now_wired or not touches(blocked, blocker):
                    continue
                there = issues.get(blocker)
                if not there:
                    # Order is the point of the feature, so an order that silently
                    # did not happen is the worst available outcome.
                    unwired.append({"blocked": blocked, "blocker": blocker,
                                    "reason": f"{blocker} has no issue, so this order "
                                              "could not be declared"})
                    tick("wiring")
                    continue
                try:
                    client.add_blocked_by(repo, issues[blocked]["number"], there["id"])
                except GitHubError as exc:
                    # GitHub refuses a link it already holds; that is the
                    # order being in place, which is what was asked for.
                    if not (exc.status == 422 and "already been taken" in str(exc)):
                        unwired.append({"blocked": blocked, "blocker": blocker,
                                        "reason": str(exc)})
                        tick("wiring")
                        continue
                now_wired.add(pair)
                wired.append({"blocked": blocked, "blocker": blocker,
                              "blocked_number": issues[blocked]["number"],
                              "blocker_id": there["id"]})
                tick("wiring")
        # Removal walks the edges as they were BEFORE this press: a blocker
        # withdrawn just now is gone from `issues`, and its edge is exactly the
        # one that must leave the tracker -- a consumer treats a blocker closed
        # `not_planned` as never satisfied.
        for pair in sorted(wired_before):
            blocked, _, blocker = pair.partition("<-")
            if (pair in wanted or blocked not in issues or blocked in frozen or blocked in synced
                    or not in_scope(blocked)):
                continue
            source = live.get(blocker)
            if not source:
                continue
            try:
                client.remove_blocked_by(repo, issues[blocked]["number"], source["id"])
            except GitHubError as exc:
                unwired.append({"blocked": blocked, "blocker": blocker,
                                "reason": f"a dropped order could not be removed: {exc}"})
                continue
            now_wired.discard(pair)
            removed.append({"blocked": blocked, "blocker": blocker})

        # Orphans: open issues of ours that no key owns -- duplicates a lost
        # record left behind, follow-ups the contract no longer calls for.
        if ours is not None and pickup and only is None:
            owned = {int(v["number"]) for v in issues.values() if v.get("number") is not None}
            # A root issue an attach reopened is ours and owned by no key, and
            # its work is merged: sweeping it would overwrite `completed`.
            owned |= {int(v["root"]["number"]) for v in issues.values()
                      if (v.get("root") or {}).get("number")}
            gone_now = set(closed.values())
            for key, found in sorted(ours.items()):
                for issue in found:
                    n = int(issue["number"])
                    if n in owned or n in gone_now:
                        continue
                    if any(l in pickup for l in issue.get("labels") or ()):
                        touched.append({"key": key, "number": n, "reason":
                                        "an open issue no unit owns any more, but it is "
                                        "picked up -- left alone"})
                        continue
                    try:
                        client.close_issue(repo, n)
                    except GitHubError as exc:
                        failed.append({"key": key, "reason": f"orphan #{n} could not be closed: {exc}"})
                        continue
                    swept.append({"key": key, "number": n, "url": issue.get("url", "")})
                    tick("sweeping")

        if ours is not None:
            verify((open_numbers | {int(i["number"]) for i in created + follow_ups})
                   - set(closed.values()) - {int(s["number"]) for s in swept})

    finally:
        # Recorded even when the run went badly: otherwise a re-run duplicates the
        # issues that DID land, which is the one thing the guard exists to stop.
        # A settled marker set or cleared counts: unrecorded, every press
        # re-reads every closed issue.
        if (created or edited or wired or removed or withdrawn or follow_ups or adopted
                or marks["changed"]):
            _append(log_path, Emission(
                seq=batch, at=now_fn(), cut_seq=cut_seq, repo=repo,
                issues=issues, wired=tuple(sorted(now_wired)), lineage=lineage,
            ))
        if closed:
            _append(log_path, Rollback(
                seq=_next_seq(log_path), at=now_fn(), cut_seq=cut_seq, repo=repo,
                undone=(), closed=closed,
            ))

    merge_order = [{"blocked": k, "blocker": b, "order": MERGE}
                   for k in order for b in orders["now"].get(k, ())]
    return {
        "emitted": True,
        "repo": repo,
        "cut_seq": cut_seq,
        "batch": batch,
        "horizon": horizon,
        "created": created,
        "edited": edited,
        "withdrawn": withdrawn,
        "touched": touched,
        "settled": settled,
        "follow_ups": follow_ups,
        "adopted": adopted,
        "swept": swept,
        "raced": raced,
        "held": held,
        "skipped": skipped,
        "failed": failed,
        "wired": wired,
        "unwired": unwired,
        "removed": removed,
        "dead": dead,
        "linked": linked,
        "unlinked": unlinked,
        "unlabelled": unlabelled,
        "only": only,
        # Derived this press, never stored as an entry edge (M31, D11).
        "merge_order": merge_order,
        # Orders a loop ran through: dropped, never a refusal (2026-10-24).
        "dropped_orders": orders["dropped"],
        "counts": {
            "units": len(order),
            "created": len(created),
            "edited": len(edited),
            "withdrawn": len(withdrawn),
            "touched": len(touched),
            "settled": len(settled),
            "follow_ups": len(follow_ups),
            "adopted": len(adopted),
            "swept": len(swept),
            "raced": len(raced),
            "held": len(held),
            "skipped": len(skipped),
            "failed": len(failed),
            "wired": len(wired),
            "unwired": len(unwired),
            "removed": len(removed),
            "dead": len(dead),
            "linked": len(linked),
            "unlinked": len(unlinked),
            "unlabelled": len(unlabelled),
            "merge_order": len(merge_order),
            "dropped_orders": sum(len(d["orders"]) for d in orders["dropped"]),
        },
    }
