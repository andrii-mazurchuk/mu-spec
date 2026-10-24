"""What happened, in order, so a project's whole life can be read back.

The graph answers *what is true now*. This answers *how it got that way* --
and those are different questions, because the state of a graph cannot tell
you what it used to say, what was refused on the way, or what somebody had to
guess.

Deliberately **not** a second copy of the graph. Each event references
identifiers rather than carrying bodies, and adds only the facts computed at
that moment which state cannot recover afterwards:

- a gate that failed and was then fixed leaves no trace in the fixed graph
- a correction's *layer of origin* is the whole point of §8's classification,
  and once the fix has propagated the graph just looks correct
- what an agent could not derive, and assumed instead
- how many slices one change had to touch

That last one is the primary score. `DESIGN.md` §9 already says the
distribution of these outcomes over time is "the debug signal for the agent
system itself" -- mostly-intent corrections mean the interview is shallow,
mostly-architecture means the prompting is weak. This is the instrumentation
that claim always assumed.

Append-only, one log per unit, never loaded during ordinary work. Reading it
is analysis, not operation.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path
from typing import Any, Callable

# The event vocabulary. Closed on purpose: an analytical unit comparing
# projects needs the same words to mean the same thing in each, and a free
# string field would drift into a hundred near-synonyms within a month.
REQUEST = "request"
DERIVATION = "derivation"
CORRECTION = "correction"
REFUSAL = "refusal"
SLICING = "slicing"
# A slice ruled cross-cutting. Written only before D6 (10-24) removed the
# type; kept so the history that holds one still reads.
CLASSIFICATION = "classification"
ISSUE_RAISED = "issue_raised"
ISSUE_CLOSED = "issue_closed"
PLAN = "plan"
AUDIT = "audit"
# A person decided the corpus was finished and cut it into work units.
# The graph cannot recover this: a cut leaves a file, not an edge.
WORK_UNITS = "work_units"
# A person shipped a cut to a tracker, or withdrew one. Outbound, so nothing
# in this unit's state says when it happened or what it touched.
TICKETS_EMITTED = "tickets_emitted"
TICKETS_ROLLED_BACK = "tickets_rolled_back"
# Any write request at all, as it arrived: who sent it, what it carried, what
# came back. Recorded once, in the HTTP layer, so no route can forget.
MUTATION = "mutation"
# A person granted or revoked an agent's standing leave to cut and Ship S/T
# repairs of shipped units (M33, D2).
DELEGATION = "delegation"
# An agent used that leave: the cut it took, the conditions as evaluated.
AUTO_SHIP = "auto_ship"

KINDS = (
    REQUEST,
    DERIVATION,
    CORRECTION,
    REFUSAL,
    SLICING,
    CLASSIFICATION,
    ISSUE_RAISED,
    ISSUE_CLOSED,
    PLAN,
    AUDIT,
    WORK_UNITS,
    TICKETS_EMITTED,
    TICKETS_ROLLED_BACK,
    MUTATION,
    DELEGATION,
    AUTO_SHIP,
)


# One writer at a time in this process: an emission records from its own
# thread while requests record from theirs, and two of them reading the same
# tail would hand out the same seq.
_APPEND = threading.Lock()


@dataclasses.dataclass(frozen=True)
class Event:
    seq: int
    at: float
    project: str | None
    kind: str
    # References, never bodies. An analytical unit that wants the text asks
    # for the entry; keeping it out here is what stops the log becoming a
    # second, staler copy of the graph.
    refs: tuple[str, ...] = ()
    # Facts computed at this moment that the graph cannot answer later.
    facts: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "project": self.project,
            "kind": self.kind,
            "refs": list(self.refs),
            "facts": self.facts,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Event":
        return cls(
            seq=raw["seq"],
            at=raw.get("at", 0.0),
            project=raw.get("project"),
            kind=raw["kind"],
            refs=tuple(raw.get("refs") or ()),
            facts=dict(raw.get("facts") or {}),
        )


class Lifecycle:
    """Append-only. Never rewritten, never compacted -- an event that turned
    out to be embarrassing is exactly the one worth keeping."""

    def __init__(self, path: Path, sink: Callable[[Any], Any] | None = None) -> None:
        self._path = Path(path)
        # Where a copy goes after the local write. Injected, optional, and
        # called last on purpose: the local log is the durable record and
        # must land whether or not anything downstream is listening.
        self._sink = sink

    def _read(self) -> list[Event]:
        if not self._path.exists():
            return []
        return [
            Event.from_json(json.loads(line))
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _last_seq(self) -> int:
        """The seq of the last line, read from the end of the file.

        Every write request now logs, so counting the whole file per event
        would make each write O(history). Lines are only ever appended and
        numbered in order, so the last line's seq is the count.
        """
        try:
            with self._path.open("rb") as handle:
                end = handle.seek(0, 2)
                size = 8192
                while True:
                    start = max(0, end - size)
                    handle.seek(start)
                    lines = [l for l in handle.read().splitlines() if l.strip()]
                    # The first line of a mid-file chunk may be partial; with
                    # two in hand the last one is whole.
                    if start == 0 or len(lines) >= 2:
                        break
                    size *= 2
        except FileNotFoundError:
            return 0
        if not lines:
            return 0
        try:
            return int(json.loads(lines[-1])["seq"])
        except (ValueError, KeyError, TypeError):
            # A torn last line. Count instead -- slow, and still right.
            return len(self._read())

    def record(
        self,
        kind: str,
        project: str | None,
        now_fn: Callable[[], float],
        /,
        refs: list[str] | tuple[str, ...] = (),
        **facts: Any,
    ) -> Event | None:
        """Append one event.

        The first three are positional-only so that a fact may be called
        anything at all -- an issue event carries its own `kind`, and a
        collision there would be a runtime error at exactly the moment
        something interesting was being recorded.

        Returns None rather than raising on an unknown kind. Recording is
        never the point of the call that triggers it -- an amendment that
        succeeded must not be reported as failed because its log line was
        malformed. The vocabulary is still closed; a bad kind is dropped,
        not silently renamed. An unwritable log returns None for the same
        reason.
        """
        if kind not in KINDS:
            return None
        try:
            with _APPEND:
                event = Event(
                    seq=self._last_seq() + 1,
                    at=now_fn(),
                    project=project,
                    kind=kind,
                    refs=tuple(str(r) for r in refs),
                    facts=facts,
                )
                line = json.dumps(event.to_json(), ensure_ascii=False, default=str)
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            return None
        return event

    def list(
        self,
        project: str | None = None,
        kind: str | None = None,
        since: int = 0,
    ) -> list[Event]:
        out = [e for e in self._read() if e.seq > since]
        if project:
            out = [e for e in out if e.project == project]
        if kind:
            out = [e for e in out if e.kind == kind]
        return out
