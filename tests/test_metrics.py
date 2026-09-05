from __future__ import annotations

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.inbox import Inbox
from mu_spec.metrics import change_locality, corrections_by_layer, structural
from mu_spec.storage import CROSS_CUTTING, Manifest, Slice


def _clock():
    return 1.0


def _inbox(tmp_path) -> Inbox:
    return Inbox(tmp_path / "inbox.jsonl")


def _manifest() -> Manifest:
    return Manifest(
        project="m",
        slices={
            "discovery": Slice(
                name="discovery", members={parse("B·01"), parse("S·01")}
            ),
            "payouts": Slice(
                name="payouts", members={parse("B·02"), parse("S·02")}
            ),
            "audit": Slice(
                name="audit", members={parse("S·03")}, type=CROSS_CUTTING
            ),
        },
    )


def _change(inbox, kind, produced, title="a change"):
    message = inbox.post(
        {"type": kind, "title": title, "project": "m"}, _clock
    )
    inbox.record_produced(message.id, produced)
    return message.id


# -- change locality ---------------------------------------------------------


def test_a_change_inside_one_slice_scores_one(tmp_path):
    inbox = _inbox(tmp_path)
    _change(inbox, "feature", ["I·01", "B·01", "S·01"])
    result = change_locality(_manifest(), inbox, "m")
    assert result["changes"] == 1
    assert result["single_slice"] == 1
    assert result["mean"] == 1.0


def test_a_change_spanning_slices_scores_higher(tmp_path):
    """The definition of a bad cut, measured rather than approximated."""
    inbox = _inbox(tmp_path)
    _change(inbox, "feature", ["I·01", "B·01", "S·01", "S·03"])
    result = change_locality(_manifest(), inbox, "m")
    assert result["distribution"] == {"2": 1}
    assert result["worst"][0]["slices"] == ["audit", "discovery"]


def test_intent_does_not_inflate_every_score(tmp_path):
    """Intent is never sliced. Counting it as a slice of its own would add
    one to every change and distinguish nothing."""
    inbox = _inbox(tmp_path)
    _change(inbox, "feature", ["I·01", "I·02", "B·01"])
    assert change_locality(_manifest(), inbox, "m")["mean"] == 1.0


def test_bookkeeping_references_are_ignored(tmp_path):
    """`project:marketplace` is recorded as produced and is not an entry."""
    inbox = _inbox(tmp_path)
    _change(inbox, "initiate", ["project:m", "I·01", "B·01"])
    assert change_locality(_manifest(), inbox, "m")["changes"] == 1


def test_a_change_that_produced_nothing_sliced_is_not_counted(tmp_path):
    inbox = _inbox(tmp_path)
    _change(inbox, "feature", ["I·01"])
    result = change_locality(_manifest(), inbox, "m")
    assert result["changes"] == 0
    assert result["mean"] is None


def test_the_worst_changes_are_named_not_just_counted(tmp_path):
    inbox = _inbox(tmp_path)
    _change(inbox, "feature", ["B·01"], title="narrow")
    wide = _change(inbox, "feature", ["B·01", "B·02", "S·03"], title="wide")
    result = change_locality(_manifest(), inbox, "m")
    assert result["worst"][0]["message"] == wide
    assert result["worst"][0]["count"] == 3


# -- correction distribution -------------------------------------------------


def test_corrections_are_counted_by_where_they_entered(tmp_path):
    """§9's debug signal. Once a fix has propagated the graph just looks
    correct, so the layer of origin has to be recorded when it happens."""
    inbox = _inbox(tmp_path)
    _change(inbox, "correction", ["I·05", "B·07"])
    _change(inbox, "correction", ["B·09"])
    result = corrections_by_layer(Graph([]), inbox, "m")
    assert result["corrections"] == 2
    assert result["by_layer"] == {"behaviour": 1, "intent": 1}


def test_only_corrections_are_counted(tmp_path):
    inbox = _inbox(tmp_path)
    _change(inbox, "feature", ["I·01"])
    assert corrections_by_layer(Graph([]), inbox, "m")["corrections"] == 0


# -- structural --------------------------------------------------------------


def _graph() -> Graph:
    return Graph(
        [
            Entry(id=parse("S·01"), title="index"),
            Entry(
                id=parse("S·02"),
                title="ledger",
                depends_on=(parse("S·01"),),
                emits_into=(parse("S·03"),),
            ),
            Entry(id=parse("S·03"), title="audit log"),
        ]
    )


def test_cohesion_counts_only_edges_that_stay_inside():
    rows = {r["slice"]: r for r in structural(_manifest(), _graph())["slices"]}
    assert rows["payouts"]["outbound_edges"] == 1
    assert rows["payouts"]["cohesion"] == 0.0
    assert rows["discovery"]["inbound_edges"] == 1


def test_emissions_are_not_counted_as_coupling():
    """They impose no order and cross into a concern by design. Counting
    them would penalise exactly what the edge exists to make cheap."""
    rows = {r["slice"]: r for r in structural(_manifest(), _graph())["slices"]}
    assert rows["payouts"]["emissions"] == 1
    assert rows["payouts"]["depends_on"] == ["discovery"]
    assert "audit" not in rows["payouts"]["depends_on"]


def test_a_slice_with_no_edges_has_no_cohesion_rather_than_zero():
    """Undefined, not bad. Reporting 0.0 would rank a brand-new slice as the
    worst thing in the project."""
    rows = {r["slice"]: r for r in structural(_manifest(), _graph())["slices"]}
    assert rows["audit"]["cohesion"] is None
