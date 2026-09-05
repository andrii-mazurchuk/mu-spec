"""The internal channel: a queue of requests against artifacts.

An agent deriving one slice discovers that another slice's entry is wrong,
missing something, or means something other than it assumed. It does not
message that slice's agent -- there is deliberately no agent-to-agent
channel. That agent is finished and gone; and even if it were live,
message-passing means either blocking or nondeterminism, and either way the
audit property is lost.

Instead it files an **issue against the artifact** and proceeds on its stated
assumption, flagging that assumption as a judgement call. The queue is
addressed to a target entry, not to a process, so it survives the session
that raised it and can be read back in any order.

Two kinds, and the difference decides what it costs:

- **additive** -- a new entry is needed. Nothing existing changes meaning, so
  nothing downstream is invalidated and nothing re-runs.
- **semantic** -- an existing entry that others already consumed now means
  something else. Its consumers are invalidated. Expensive, correctly so, and
  rare if the slicing was good.

Which one an issue is, is the raiser's claim -- it is a judgement about
meaning, so this unit records it rather than deciding it. What the unit
computes is the *consequence*: exactly which entries consumed the old
meaning, from the entries' own edges.

Nothing here dispatches anything. mu-spec never spawns a process. It holds
the queue, groups it, and computes the scope; whatever runs a repair session
reads that and lives elsewhere.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Callable

ADDITIVE = "additive"
SEMANTIC = "semantic"
KINDS = (ADDITIVE, SEMANTIC)

OPEN = "open"
RESOLVED = "resolved"
ESCALATED = "escalated"
STATUSES = (OPEN, RESOLVED, ESCALATED)

# A repair can raise its own issues, which can raise their own. Without a cap
# the system oscillates and nobody notices until it has burned a day. Two
# rounds, then it stops being a cascade and becomes a question for a human.
MAX_ROUNDS = 2


class IssueError(ValueError):
    """A malformed issue. A hard error rather than a degraded default: an
    issue that silently loses its target is a repair nobody will ever make."""


@dataclasses.dataclass(frozen=True)
class Issue:
    id: str
    project: str
    # The entry this is about. An issue is always against an artifact --
    # that is what makes it outlive the session that raised it.
    target: str
    # Filled in from the manifest when the issue is filed, so grouping does
    # not have to re-resolve membership that may since have moved.
    target_slice: str | None
    # Who noticed. Kept for the diagnostic distribution, not for addressing:
    # nothing is ever sent back to it.
    raised_by: str | None
    kind: str
    claim: str = ""
    # The assumption the raiser proceeded on. This is the judgement call it
    # is obliged to flag, carried with the issue so a reviewer sees the
    # request and what was assumed in its absence together.
    assumption: str = ""
    round: int = 1
    status: str = OPEN
    at: float = 0.0
    resolution: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "target": self.target,
            "target_slice": self.target_slice,
            "raised_by": self.raised_by,
            "kind": self.kind,
            "claim": self.claim,
            "assumption": self.assumption,
            "round": self.round,
            "status": self.status,
            "at": self.at,
            "resolution": self.resolution,
        }

    def header(self) -> dict[str, Any]:
        """What the router reads: target, requester, kind, one-line claim.

        Roughly thirty tokens. The router never opens the assumption or the
        target's body -- that is what keeps its context flat however many
        issues there are, and the expensive reading happens later in a
        session that was going to load that column anyway.
        """
        return {
            "id": self.id,
            "target": self.target,
            "target_slice": self.target_slice,
            "raised_by": self.raised_by,
            "kind": self.kind,
            "claim": self.claim,
            "round": self.round,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Issue":
        return cls(
            id=raw["id"],
            project=raw["project"],
            target=raw["target"],
            target_slice=raw.get("target_slice"),
            raised_by=raw.get("raised_by"),
            kind=raw.get("kind", ADDITIVE),
            claim=raw.get("claim", ""),
            assumption=raw.get("assumption", ""),
            round=raw.get("round", 1),
            status=raw.get("status", OPEN),
            at=raw.get("at", 0.0),
            resolution=raw.get("resolution"),
        )


class IssueLog:
    """One append-only log per unit, parsed on every read. Small, and a stale
    in-memory copy is worse than a re-read when another process may have
    appended."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def _read(self) -> list[Issue]:
        if not self._path.exists():
            return []
        return [
            Issue.from_json(json.loads(line))
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _write(self, issues: list[Issue]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            "".join(json.dumps(i.to_json()) + "\n" for i in issues),
            encoding="utf-8",
        )

    def raise_issue(
        self,
        body: dict,
        target_slice: str | None,
        now_fn: Callable[[], float],
    ) -> Issue:
        project = body.get("project")
        if not isinstance(project, str) or not project.strip():
            raise IssueError("'project' is required")
        target = body.get("target")
        if not isinstance(target, str) or not target.strip():
            raise IssueError(
                "'target' is required -- an issue is filed against an entry, "
                "never sent to an agent"
            )
        kind = body.get("kind")
        if kind not in KINDS:
            raise IssueError(f"'kind' must be one of {KINDS}")
        claim = body.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            raise IssueError(
                "'claim' is required and should fit on one line -- the router "
                "reads headers only"
            )

        issues = self._read()
        issue = Issue(
            id=f"iss-{len(issues) + 1:04d}",
            project=project,
            target=target.strip(),
            target_slice=target_slice,
            raised_by=body.get("raised_by"),
            kind=kind,
            claim=claim.strip(),
            assumption=body.get("assumption", ""),
            round=max(1, int(body.get("round", 1))),
            at=now_fn(),
        )
        issues.append(issue)
        self._write(issues)
        return issue

    def get(self, issue_id: str) -> Issue | None:
        return next((i for i in self._read() if i.id == issue_id), None)

    def list(
        self,
        project: str | None = None,
        status: str | None = None,
        target_slice: str | None = None,
    ) -> list[Issue]:
        out = self._read()
        if project:
            out = [i for i in out if i.project == project]
        if status:
            out = [i for i in out if i.status == status]
        if target_slice:
            out = [i for i in out if i.target_slice == target_slice]
        return out

    def close(
        self, issue_id: str, status: str, note: str = "", produced: list[str] = ()
    ) -> Issue:
        if status not in (RESOLVED, ESCALATED):
            raise IssueError(
                f"closing status must be {RESOLVED!r} or {ESCALATED!r}"
            )
        issues = self._read()
        for index, issue in enumerate(issues):
            if issue.id != issue_id:
                continue
            if issue.status != OPEN:
                raise IssueError(f"{issue_id} is already {issue.status}")
            closed = dataclasses.replace(
                issue,
                status=status,
                resolution={"note": note, "produced": list(produced)},
            )
            issues[index] = closed
            self._write(issues)
            return closed
        raise IssueError(f"unknown issue {issue_id!r}")
