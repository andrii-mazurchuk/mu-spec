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
CLASSIFICATION = "classification"
ISSUE_RAISED = "issue_raised"
ISSUE_CLOSED = "issue_closed"
PLAN = "plan"
AUDIT = "audit"

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
)


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
        not silently renamed.
        """
        if kind not in KINDS:
            return None
        events = self._read()
        event = Event(
            seq=len(events) + 1,
            at=now_fn(),
            project=project,
            kind=kind,
            refs=tuple(str(r) for r in refs),
            facts=facts,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
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
