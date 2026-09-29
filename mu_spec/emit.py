"""A cut becomes issues on a tracker: two passes, and a log of what landed.

This is the only module in the unit that causes something to happen outside
it, and the boundary it sits on is worth stating precisely. mu-spec **creates**
tickets -- that is its purpose -- and never **acts on** them. What happens to
an issue after it exists is the tracker's business and the business of whoever
picks the work up. Nothing here reads an issue back, comments, closes, or
reacts to a specification that has since moved.

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
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from mu_spec.github import GitHubError
from mu_spec.render import render


# Every line in the log carries a kind. An emission omits it, because the
# log predates there being more than one kind and rewriting history to add a
# field is precisely what this unit does not do.
ROLLBACK = "rollback"


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

    def to_json(self) -> dict:
        return {
            "seq": self.seq,
            "at": self.at,
            "cut_seq": self.cut_seq,
            "repo": self.repo,
            "issues": self.issues,
            "wired": list(self.wired),
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
                    closed={str(k): int(v) for k, v in (raw.get("closed") or {}).items()},
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
                    issues=dict(raw.get("issues") or {}),
                    wired=tuple(raw.get("wired") or ()),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise EmissionError(f"{path}:{number} is not an emission: {exc}") from exc
    return tuple(out)


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
                "step", "total", "processed", "created", "skipped",
                "failed", "wired", "unwired",
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

    def start(self, project: str, work: Callable[[Callable[[dict], None]], dict]) -> Run:
        """Begin an emission. `work` is handed a `report` callback.

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
                return
            run.finish(result, None, self._now())

        self._spawn(body)
        return run

    def status(self, project: str) -> dict | None:
        with self._lock:
            run = self._runs.get(project)
        return run.snapshot() if run else None


def rollback(
    *,
    log_path: Path,
    repo: str,
    cut_seq: int,
    client,
    now_fn: Callable[[], float],
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Withdraw every issue this unit created for one cut, on this repository.

    Scoped by `(cut_seq, repo)` because that is exactly the key the duplicate
    guard uses. One concept, not two: what a re-emission would skip is what a
    rollback undoes, and the two staying in step is not an accident that has
    to be maintained.

    Closed, never deleted. REST cannot delete an issue; that is an admin-only
    GraphQL mutation. Closing is also the reversible half, which is the right
    default for an operation whose entire purpose is undoing something -- and
    a closed issue leaves the default `is:open` list, which is the visible
    outcome somebody asking for a rollback actually wants.

    Nothing raises. A run of 144 closes has to come back as a pass/fail list,
    exactly like the emission it undoes, not as one exception about the first
    issue GitHub would not touch.

    **The one thing this does NOT do is decide.** It withdraws what a person
    asked to withdraw. Nothing here reads an issue's state back, reacts to a
    comment, or concludes on its own that a cut is stale -- that is the line
    this unit does not cross, and a rollback sits on the near side of it
    because a person triggered it and the set was already recorded.
    """
    emissions, done = read_log(log_path)
    mine = [e for e in emissions if e.cut_seq == cut_seq and e.repo == repo]
    # By issue NUMBER, never by unit key. A unit withdrawn and then emitted
    # again is a different issue under the same key, and keying this by the
    # key made that second issue permanently un-withdrawable -- the first
    # rollback had already claimed the name. Found by driving two full rounds
    # against dark; the second rollback closed nothing and reported success.
    withdrawn: set[int] = set()
    for record in done:
        if record.cut_seq == cut_seq and record.repo == repo:
            withdrawn.update(record.closed.values())

    # Highest issue number first. An emission creates a test unit before the
    # implementation that follows it, so closing in reverse withdraws the
    # dependent before the thing it depends on and never leaves a live issue
    # blocked by a withdrawn one, however far the run gets.
    targets: dict[str, dict] = {}
    for emission in mine:
        for key, issue in emission.issues.items():
            number = issue.get("number")
            if number is not None and int(number) not in withdrawn:
                targets[key] = issue
            else:
                # A later emission may have replaced a withdrawn issue under
                # the same key; only the withdrawn one drops out.
                targets.pop(key, None)
    order = sorted(targets, key=lambda k: -int(targets[k].get("number") or 0))

    if not order:
        return {
            "rolled_back": False,
            "reason": (
                f"nothing to withdraw: no issue from cut {cut_seq} on {repo} "
                "is still recorded as open by this unit"
            ),
            "repo": repo,
            "cut_seq": cut_seq,
        }

    closed: dict[str, int] = {}
    failed: list[dict] = []
    total = len(order)

    def tick(step: str) -> None:
        if on_progress:
            on_progress({
                "step": step,
                "total": total,
                "processed": len(closed) + len(failed),
                "closed": len(closed),
                "failed": len(failed),
            })

    tick("closing")
    for key in order:
        number = targets[key].get("number")
        try:
            client.close_issue(repo, int(number))
        except (GitHubError, TypeError, ValueError) as exc:
            # Left OUT of `closed`, so the duplicate guard still considers it
            # emitted. An issue that is still open must never be recreated --
            # that turns one stray issue into two, which is worse than the
            # thing being fixed.
            failed.append({"key": key, "number": number, "reason": str(exc)})
        else:
            closed[key] = int(number)
        tick("closing")

    if closed or failed:
        _append(
            log_path,
            Rollback(
                seq=_next_seq(log_path),
                at=now_fn(),
                cut_seq=cut_seq,
                repo=repo,
                undone=tuple(sorted(e.seq for e in mine)),
                closed=closed,
                failed=tuple(failed),
            ),
        )

    return {
        "rolled_back": True,
        "repo": repo,
        "cut_seq": cut_seq,
        "closed": [{"key": k, "number": n} for k, n in closed.items()],
        "failed": failed,
        "counts": {
            "targeted": total,
            "closed": len(closed),
            "failed": len(failed),
        },
    }


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
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Create an issue per work unit, then wire the dependencies between them.

    `order` is the unit keys in dependency order; `edges` maps a unit to the
    units it follows. `fetch` returns one work unit's payload -- injected rather
    than taken from a store, so the loop is testable and so this module needs to
    know nothing about how a unit is assembled.
    """
    previous, rollbacks = read_log(log_path)
    known: dict[str, dict] = {}
    wired_already: set[str] = set()
    for emission in previous:
        if emission.cut_seq == cut_seq and emission.repo == repo:
            known.update(emission.issues)
            wired_already.update(emission.wired)
    # A withdrawn unit is emittable again -- that is the whole point of a
    # rollback, and without this the guard would skip every unit whose issue
    # is now closed and the re-emission would create nothing.
    #
    # Matched on the issue NUMBER the guard currently holds, not on the key: a
    # unit emitted, withdrawn and emitted again has a live issue under a key
    # that also appears in a rollback, and dropping it by name would create a
    # third issue for work that already has one.
    closed_numbers: set[int] = set()
    for rollback in rollbacks:
        if rollback.cut_seq == cut_seq and rollback.repo == repo:
            closed_numbers.update(rollback.closed.values())
    reopened = {
        key for key, issue in known.items()
        if issue.get("number") is not None
        and int(issue["number"]) in closed_numbers
    }
    for key in reopened:
        known.pop(key, None)
    # The edges go with them: an edge wired between two issues that are now
    # closed must be declared again between their replacements.
    wired_already = {
        pair for pair in wired_already
        if not any(key in pair.split("<-") for key in reopened)
    }

    if not order:
        return {
            "emitted": False,
            "reason": "there are no work units in this cut to emit",
            "repo": repo,
            "cut_seq": cut_seq,
        }

    created: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    issues: dict[str, dict] = dict(known)
    wired: list[dict] = []
    unwired: list[dict] = []

    def tick(step: str, processed: int) -> None:
        """Report after every unit and every edge.

        Per item rather than per phase: the point is watching a six-minute run
        advance, and two updates in six minutes is a spinner with extra steps.
        """
        if on_progress is None:
            return
        on_progress({
            "step": step,
            "total": len(order),
            "processed": processed,
            "created": len(created),
            "skipped": len(skipped),
            "failed": len(failed),
            "wired": len(wired),
            "unwired": len(unwired),
        })

    # -- pass one: the issues ----------------------------------------------
    for position, key in enumerate(order, start=1):
        if key in known:
            skipped.append({
                "key": key,
                "reason": f"already emitted as #{known[key].get('number')}",
                "number": known[key].get("number"),
                "url": known[key].get("url", ""),
            })
            tick("creating", position)
            continue
        try:
            ticket = render(fetch(key))
        except (ValueError, KeyError) as exc:
            # An unsound graph refuses one unit at a time. One bad unit must
            # not cost the other hundred and forty-three.
            failed.append({"key": key, "reason": f"could not be rendered: {exc}"})
            tick("creating", position)
            continue
        if ticket.oversized:
            skipped.append({
                "key": key,
                "reason": f"the rendered body is too large for the tracker "
                          f"({len(ticket.body)} characters)",
            })
            tick("creating", position)
            continue
        try:
            issue = client.create_issue(
                repo, ticket.title, ticket.body, ticket.labels
            )
        except GitHubError as exc:
            failed.append({"key": key, "reason": str(exc)})
            tick("creating", position)
            continue
        issues[key] = {"id": issue.id, "number": issue.number, "url": issue.url}
        created.append({
            "key": key,
            "title": ticket.title,
            "number": issue.number,
            "url": issue.url,
            "labels": list(ticket.labels),
        })
        tick("creating", position)

    # -- pass two: the order ------------------------------------------------
    for blocked in order:
        for blocker in edges.get(blocked, ()) or ():
            pair = _pair(blocked, blocker)
            if pair in wired_already:
                continue
            here, there = issues.get(blocked), issues.get(blocker)
            if not here or not there:
                # Order is the point of the feature, so an order that silently
                # did not happen is the worst available outcome.
                missing = blocker if not there else blocked
                unwired.append({
                    "blocked": blocked,
                    "blocker": blocker,
                    "reason": f"{missing} has no issue, so this order could "
                              "not be declared",
                })
                tick("wiring", len(order))
                continue
            try:
                client.add_blocked_by(repo, here["number"], there["id"])
            except GitHubError as exc:
                unwired.append({
                    "blocked": blocked, "blocker": blocker, "reason": str(exc)
                })
                tick("wiring", len(order))
                continue
            wired_already.add(pair)
            wired.append({
                "blocked": blocked,
                "blocker": blocker,
                "blocked_number": here["number"],
                "blocker_id": there["id"],
            })
            tick("wiring", len(order))

    # Recorded even when the run went badly: otherwise a re-run duplicates the
    # issues that DID land, which is the one thing the guard exists to stop.
    if created or wired:
        _append(
            log_path,
            Emission(
                seq=_next_seq(log_path),
                at=now_fn(),
                cut_seq=cut_seq,
                repo=repo,
                issues=issues,
                wired=tuple(sorted(wired_already)),
            ),
        )

    return {
        "emitted": True,
        "repo": repo,
        "cut_seq": cut_seq,
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "wired": wired,
        "unwired": unwired,
        "counts": {
            "units": len(order),
            "created": len(created),
            "skipped": len(skipped),
            "failed": len(failed),
            "wired": len(wired),
            "unwired": len(unwired),
        },
    }
