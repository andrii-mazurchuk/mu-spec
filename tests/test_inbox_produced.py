from __future__ import annotations

from mu_spec.inbox import ACCEPTED, Inbox


def _clock():
    return 1.0


def test_resolving_keeps_what_amendments_already_produced(tmp_path):
    """A resolve with no ids used to replace the list the amendments had
    recorded -- the DARK msg-0056 trap. It merges now, one spelling per id."""
    inbox = Inbox(tmp_path / "inbox.jsonl")
    message = inbox.post({"type": "correction", "title": "fix", "project": "m"}, _clock)
    inbox.record_produced(message.id, ["S-06"])
    inbox.record_produced(message.id, ["S·06", "S-07"])
    resolved = inbox.resolve(message.id, ACCEPTED, "done")
    assert resolved.resolution["produced"] == ["S-06", "S-07"]
