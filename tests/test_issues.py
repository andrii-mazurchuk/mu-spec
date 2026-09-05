from __future__ import annotations

import pytest

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.issues import (
    ADDITIVE,
    ESCALATED,
    OPEN,
    RESOLVED,
    SEMANTIC,
    IssueError,
    IssueLog,
)
from mu_spec.reconcile import REACHES_BACK, ROUND_CAP, rerun_scope, route
from mu_spec.storage import Manifest, Slice


def _log(tmp_path) -> IssueLog:
    return IssueLog(tmp_path / "issues.jsonl")


def _body(**kw) -> dict:
    return {
        "project": "m",
        "target": "S·01",
        "kind": ADDITIVE,
        "claim": "the index has no way to report its size",
        **kw,
    }


def _clock():
    return 1234.5


# -- the log -----------------------------------------------------------------


def test_an_issue_is_filed_against_an_entry(tmp_path):
    issue = _log(tmp_path).raise_issue(_body(), "listings", _clock)
    assert (issue.id, issue.target, issue.target_slice) == (
        "iss-0001",
        "S·01",
        "listings",
    )
    assert issue.status == OPEN


def test_issues_are_numbered_in_order(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(), "listings", _clock)
    second = log.raise_issue(_body(), "listings", _clock)
    assert second.id == "iss-0002"


def test_an_issue_survives_the_session_that_raised_it(tmp_path):
    """A queue against files, not an inbox between processes. The agent that
    raised it is gone by the time anyone reads it."""
    _log(tmp_path).raise_issue(_body(raised_by="payouts"), "listings", _clock)
    reloaded = IssueLog(tmp_path / "issues.jsonl").list(project="m")
    assert [i.raised_by for i in reloaded] == ["payouts"]


@pytest.mark.parametrize(
    "bad",
    [
        {"target": ""},
        {"target": None},
        {"kind": "sort-of-important"},
        {"claim": ""},
        {"project": ""},
    ],
)
def test_a_malformed_issue_is_refused(tmp_path, bad):
    with pytest.raises(IssueError):
        _log(tmp_path).raise_issue(_body(**bad), "listings", _clock)


def test_the_header_is_what_the_router_reads(tmp_path):
    """Target, requester, kind, one-line claim. Never the assumption, never
    the target's body -- that is what keeps the router's context flat."""
    issue = _log(tmp_path).raise_issue(
        _body(raised_by="payouts", assumption="assumed it returns a count"),
        "listings",
        _clock,
    )
    assert set(issue.header()) == {
        "id",
        "target",
        "target_slice",
        "raised_by",
        "kind",
        "claim",
        "round",
    }
    assert "assumption" not in issue.header()


def test_the_assumption_is_kept_even_though_the_router_ignores_it(tmp_path):
    """It is the judgement call the raiser is obliged to flag. A reviewer
    sees the request and what was assumed in its absence together."""
    issue = _log(tmp_path).raise_issue(
        _body(assumption="assumed it returns a count"), "listings", _clock
    )
    assert issue.assumption == "assumed it returns a count"


def test_closing_an_issue_records_what_it_produced(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(), "listings", _clock)
    closed = log.close("iss-0001", RESOLVED, "added a size method", ["S·09"])
    assert closed.status == RESOLVED
    assert closed.resolution["produced"] == ["S·09"]


def test_closing_twice_is_refused(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(), "listings", _clock)
    log.close("iss-0001", RESOLVED)
    with pytest.raises(IssueError):
        log.close("iss-0001", RESOLVED)


def test_listing_filters_by_status_and_target_slice(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(), "listings", _clock)
    log.raise_issue(_body(target="S·02"), "payouts", _clock)
    log.close("iss-0001", RESOLVED)
    assert [i.id for i in log.list(status=OPEN)] == ["iss-0002"]
    assert [i.id for i in log.list(target_slice="listings")] == ["iss-0001"]


# -- re-run scope ------------------------------------------------------------


def _graph() -> Graph:
    return Graph(
        [
            Entry(id=parse("S·01"), title="index"),
            Entry(id=parse("S·02"), title="ledger", depends_on=(parse("S·01"),)),
            Entry(id=parse("S·03"), title="report", depends_on=(parse("S·02"),)),
            Entry(id=parse("S·04"), title="unrelated"),
        ]
    )


def test_rerun_scope_does_not_cascade_transitively():
    """Direct dependents only. S·03 consumed S·02's meaning, not S·01's, and
    whether S·02's meaning actually moves is not known until S·02 is
    repaired -- assuming it does would re-run half the project on every
    correction."""
    assert rerun_scope(_graph(), ["S·01"]) == ("S·02",)


def test_rerun_scope_of_something_nothing_uses_is_empty():
    assert rerun_scope(_graph(), ["S·04"]) == ()


def test_an_unparseable_target_has_no_scope_but_is_not_an_error():
    """Still a real request for a human; it just has nothing computable."""
    assert rerun_scope(_graph(), ["not-an-id"]) == ()


# -- routing -----------------------------------------------------------------


def _manifest() -> Manifest:
    return Manifest(
        project="m",
        slices={
            "listings": Slice(name="listings", members={parse("S·01")}),
            "payouts": Slice(
                name="payouts", members={parse("S·02"), parse("S·03")}
            ),
            "aside": Slice(name="aside", members={parse("S·04")}),
        },
    )


def test_issues_against_one_slice_become_one_batch(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(claim="one"), "listings", _clock)
    log.raise_issue(_body(claim="two"), "listings", _clock)
    batches, escalations = route(_manifest(), _graph(), log.list())
    assert len(batches) == 1
    assert [i.claim for i in batches[0].issues] == ["one", "two"]
    assert escalations == []


def test_a_hundred_issues_across_six_slices_is_six_batches(tmp_path):
    log = _log(tmp_path)
    for n in range(50):
        log.raise_issue(_body(claim=f"c{n}"), "listings", _clock)
        log.raise_issue(_body(target="S·02", claim=f"c{n}"), "payouts", _clock)
    batches, _ = route(_manifest(), _graph(), log.list())
    assert [b.slice for b in batches] == ["listings", "payouts"]
    assert [len(b.issues) for b in batches] == [50, 50]


def test_batches_come_back_in_dependency_order(tmp_path):
    """payouts depends on listings, so listings is repaired first."""
    log = _log(tmp_path)
    log.raise_issue(_body(target="S·02"), "payouts", _clock)
    log.raise_issue(_body(target="S·01"), "listings", _clock)
    batches, _ = route(_manifest(), _graph(), log.list())
    assert [(b.slice, b.wave) for b in batches] == [("listings", 0), ("payouts", 1)]


def test_a_semantic_issue_carries_its_rerun_scope(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(kind=SEMANTIC), "listings", _clock)
    batches, _ = route(_manifest(), _graph(), log.list())
    assert batches[0].rerun == ("S·02",)


def test_an_additive_issue_invalidates_nothing(tmp_path):
    """A new entry changes no existing meaning, so nothing re-runs. This is
    why the two kinds are worth telling apart at all."""
    log = _log(tmp_path)
    log.raise_issue(_body(kind=ADDITIVE), "listings", _clock)
    batches, _ = route(_manifest(), _graph(), log.list())
    assert batches[0].rerun == ()


def test_an_issue_past_the_round_cap_is_escalated(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(round=3), "listings", _clock)
    batches, escalations = route(_manifest(), _graph(), log.list())
    assert batches == []
    assert (escalations[0].issue, escalations[0].reason) == ("iss-0001", ROUND_CAP)


def test_the_second_round_still_routes(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(_body(round=2), "listings", _clock)
    batches, escalations = route(_manifest(), _graph(), log.list())
    assert len(batches) == 1
    assert escalations == []


def test_a_semantic_issue_reaching_back_into_a_finished_wave_is_escalated(tmp_path):
    """payouts is wave 1 and says a wave-0 entry means something else. That
    wave is complete, so everything derived from it since is suspect -- a
    conflict, not a cascade."""
    log = _log(tmp_path)
    log.raise_issue(
        _body(kind=SEMANTIC, raised_by="payouts"), "listings", _clock
    )
    batches, escalations = route(_manifest(), _graph(), log.list())
    assert batches == []
    assert escalations[0].reason == REACHES_BACK
    assert "wave 1" in escalations[0].detail


def test_an_additive_issue_reaching_back_is_an_ordinary_repair(tmp_path):
    """It invalidates nothing, so nothing behind it moves and there is
    nothing for a human to rule on."""
    log = _log(tmp_path)
    log.raise_issue(
        _body(kind=ADDITIVE, raised_by="payouts"), "listings", _clock
    )
    batches, escalations = route(_manifest(), _graph(), log.list())
    assert [b.slice for b in batches] == ["listings"]
    assert escalations == []


def test_a_semantic_issue_reaching_forward_is_an_ordinary_repair(tmp_path):
    """listings is wave 0 raising against payouts in wave 1, which has not
    been derived yet. Nothing is invalidated because nothing was built."""
    log = _log(tmp_path)
    log.raise_issue(
        _body(target="S·02", kind=SEMANTIC, raised_by="listings"),
        "payouts",
        _clock,
    )
    batches, escalations = route(_manifest(), _graph(), log.list())
    assert [b.slice for b in batches] == ["payouts"]
    assert escalations == []


def test_an_issue_within_one_slice_never_reaches_back(tmp_path):
    log = _log(tmp_path)
    log.raise_issue(
        _body(kind=SEMANTIC, raised_by="listings"), "listings", _clock
    )
    _, escalations = route(_manifest(), _graph(), log.list())
    assert escalations == []


def test_routing_an_empty_queue_produces_nothing(tmp_path):
    assert route(_manifest(), _graph(), []) == ([], [])
