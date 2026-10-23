"""The lifecycle log: closed vocabulary, append-only, numbered from its tail."""

from __future__ import annotations

import pytest

from mu_spec import lifecycle as lc
from mu_spec.lifecycle import Lifecycle


@pytest.mark.parametrize("kind", [lc.TICKETS_EMITTED, lc.TICKETS_ROLLED_BACK, lc.MUTATION])
def test_ticket_and_mutation_events_are_recorded_not_dropped(tmp_path, kind):
    """`tickets_emitted` used to be passed as a bare string outside the
    vocabulary, so `record` dropped every one."""
    log = Lifecycle(tmp_path / "l.jsonl")
    assert log.record(kind, "m", lambda: 1.0) is not None
    assert [e.kind for e in log.list()] == [kind]


def test_sequence_continues_across_instances_from_the_tail(tmp_path):
    path = tmp_path / "l.jsonl"
    for _ in range(3):
        Lifecycle(path).record(lc.MUTATION, "m", lambda: 1.0, body="z" * 5000)
    assert Lifecycle(path).record(lc.MUTATION, "m", lambda: 1.0).seq == 4
    assert [e.seq for e in Lifecycle(path).list()] == [1, 2, 3, 4]


def test_an_unwritable_log_returns_none_instead_of_raising(tmp_path):
    path = tmp_path / "l.jsonl"
    path.mkdir()  # appending to a directory raises
    assert Lifecycle(path).record(lc.MUTATION, "m", lambda: 1.0) is None
