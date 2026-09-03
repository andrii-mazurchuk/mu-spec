"""The single door into this unit from outside.

Every change the outside world wants enters here as a *request*, never as a
write. Before this existed there were four write endpoints, and one of them
took a `layer` argument -- so a caller could send `{"layer": "S"}` and patch
the spec directly while intent, behaviour and architecture went on saying
something else. That is exactly the failure the pipeline exists to prevent,
sitting in the API as a feature.

A message says what someone wants. What actually changes, and where, is
decided by the agent that processes the inbox and submits amendments. The
unit records and constrains; it does not reason.

**The type carries the permission.** A requester never names a layer, so
there is no way to express "write to spec" -- the vocabulary does not
contain it. `originates_at` is the deepest an accepted message may reach
when the first entry is created in response to it; propagation downward from
there is unrestricted, because that is the pipeline doing its job.

Messages are held in one append-only log for the whole unit rather than per
project, because `initiate` has no project yet by definition.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Callable

INBOX_FILE = "inbox.jsonl"

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
STATUSES = (PENDING, ACCEPTED, REJECTED)


@dataclasses.dataclass(frozen=True)
class MessageType:
    name: str
    # The layers at which the FIRST entry created in response may sit. Empty
    # means this type may not create entries at all.
    originates_at: tuple[str, ...]
    creates_project: bool
    description: str


TYPES: dict[str, MessageType] = {
    t.name: t
    for t in (
        MessageType(
            "initiate",
            ("I",),
            True,
            "Start a new project. The body is the raw idea; the agent "
            "interviews for the rest before any intent entry is written.",
        ),
        MessageType(
            "feature",
            ("I",),
            False,
            "Ask for something the product does not do yet. Originates at "
            "intent, because a new capability is a new requirement.",
        ),
        MessageType(
            "correction",
            ("I", "B"),
            False,
            "Say that something is wrong. Originates at intent (we "
            "misunderstood what you wanted) or behaviour (we understood you "
            "and described the wrong thing). It may not originate lower: "
            "patching a decision or a spec while the layers above still say "
            "the old thing is how the artifacts start lying.",
        ),
        MessageType(
            "comment",
            (),
            False,
            "Attach a question or observation to part of the design. Changes "
            "nothing on its own, and can never silently become a decision.",
        ),
        MessageType(
            "question",
            (),
            False,
            "Ask something that needs an answer rather than a change.",
        ),
    )
}


class InboxError(ValueError):
    """A malformed or impermissible message."""


@dataclasses.dataclass(frozen=True)
class Message:
    id: str
    type: str
    title: str
    body: str = ""
    project: str | None = None
    # Deliberately optional, and usually absent. Whoever is asking generally
    # cannot know how the design is laid out -- that is the whole reason they
    # are asking. Requiring a target would push the unit's internal structure
    # onto the outside world, which is the coupling this system exists to
    # avoid. When present it is a hint the processing agent may use.
    targets: tuple[str, ...] = ()
    origin: str = "unknown"
    status: str = PENDING
    at: float = 0.0
    # Set when the message is resolved: what it produced, or why it did not.
    resolution: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "project": self.project,
            "targets": list(self.targets),
            "origin": self.origin,
            "status": self.status,
            "at": self.at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Message":
        return cls(
            id=raw["id"],
            type=raw["type"],
            title=raw.get("title", ""),
            body=raw.get("body", ""),
            project=raw.get("project"),
            targets=tuple(raw.get("targets") or ()),
            origin=raw.get("origin", "unknown"),
            status=raw.get("status", PENDING),
            at=raw.get("at", 0.0),
            resolution=raw.get("resolution"),
        )


class Inbox:
    """One log for the whole unit. Reads parse the file each time -- it is
    small, and a stale in-memory copy is worse than a re-read when another
    process may have appended."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def _read(self) -> list[Message]:
        if not self._path.exists():
            return []
        return [
            Message.from_json(json.loads(line))
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _write(self, messages: list[Message]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            "".join(json.dumps(m.to_json()) + "\n" for m in messages),
            encoding="utf-8",
        )

    def post(self, body: dict, now_fn: Callable[[], float]) -> Message:
        kind = body.get("type")
        if kind not in TYPES:
            raise InboxError(
                f"'type' must be one of {tuple(TYPES)} -- the type is what "
                "decides how deep a change may reach, so there is no way to "
                "ask for a spec edit directly"
            )
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            raise InboxError("'title' is required")

        spec = TYPES[kind]
        project = body.get("project")
        if spec.creates_project:
            if project is not None and not isinstance(project, str):
                raise InboxError("'project' must be a string when given")
        elif not isinstance(project, str) or not project.strip():
            raise InboxError(f"'project' is required for a {kind!r} message")

        targets = body.get("targets") or []
        if not isinstance(targets, list) or not all(
            isinstance(t, str) for t in targets
        ):
            raise InboxError("'targets' must be a list of identifiers when given")

        messages = self._read()
        message = Message(
            id=f"msg-{len(messages) + 1:04d}",
            type=kind,
            title=title.strip(),
            body=body.get("body", ""),
            project=project,
            targets=tuple(targets),
            origin=body.get("origin", "unknown"),
            status=PENDING,
            at=now_fn(),
        )
        messages.append(message)
        self._write(messages)
        return message

    def get(self, message_id: str) -> Message | None:
        return next((m for m in self._read() if m.id == message_id), None)

    def list(
        self,
        status: str | None = None,
        project: str | None = None,
        kind: str | None = None,
        target: str | None = None,
    ) -> list[Message]:
        out = self._read()
        if status:
            out = [m for m in out if m.status == status]
        if project:
            out = [m for m in out if m.project == project]
        if kind:
            out = [m for m in out if m.type == kind]
        if target:
            out = [m for m in out if target in m.targets]
        return out

    def resolve(
        self, message_id: str, status: str, note: str = "", produced: list[str] = ()
    ) -> Message:
        if status not in (ACCEPTED, REJECTED):
            raise InboxError(f"resolution status must be {ACCEPTED!r} or {REJECTED!r}")
        messages = self._read()
        for index, message in enumerate(messages):
            if message.id != message_id:
                continue
            if message.status != PENDING:
                raise InboxError(f"{message_id} is already {message.status}")
            resolved = dataclasses.replace(
                message,
                status=status,
                resolution={"note": note, "produced": list(produced)},
            )
            messages[index] = resolved
            self._write(messages)
            return resolved
        raise InboxError(f"unknown message {message_id!r}")

    def record_produced(self, message_id: str, produced: list[str]) -> None:
        """Append identifiers an amendment created in response to a message,
        without resolving it -- propagation usually takes several amendments
        before the request is actually satisfied."""
        messages = self._read()
        for index, message in enumerate(messages):
            if message.id == message_id:
                existing = (message.resolution or {}).get("produced", [])
                messages[index] = dataclasses.replace(
                    message,
                    resolution={
                        **(message.resolution or {"note": ""}),
                        "produced": existing + list(produced),
                    },
                )
                self._write(messages)
                return
