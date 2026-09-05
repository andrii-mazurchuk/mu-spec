from __future__ import annotations

import pytest

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.slicing import candidates, score
from mu_spec.storage import CROSS_CUTTING, Manifest


def _behaviour() -> Graph:
    """Three behaviours under two intent entries: B·01 and B·02 share I·01,
    B·03 sits under I·02, and B·04 hangs off both."""
    return Graph(
        [
            Entry(id=parse("I·01"), title="buyers find sellers"),
            Entry(id=parse("I·02"), title="sellers get paid"),
            Entry(id=parse("B·01"), derives_from=(parse("I·01"),), title="search"),
            Entry(id=parse("B·02"), derives_from=(parse("I·01"),), title="filter"),
            Entry(id=parse("B·03"), derives_from=(parse("I·02"),), title="payout"),
            Entry(
                id=parse("B·04"),
                derives_from=(parse("I·01"), parse("I·02")),
                title="receipts",
            ),
        ]
    )


def _spec() -> Graph:
    return Graph(
        [
            Entry(id=parse("S·01"), title="index"),
            Entry(id=parse("S·02"), title="ledger", depends_on=(parse("S·01"),)),
            Entry(id=parse("S·03"), title="audit"),
        ]
    )


# -- candidates --------------------------------------------------------------


def test_shared_parentage_is_reported_pairwise():
    """The strongest ex-ante signal, and free -- the edge is already there."""
    result = candidates(_behaviour())
    top = result["shared_parentage"][0]
    assert top["pair"] == ["B·01", "B·02"]
    assert top["shared_parents"] == ["I·01"]


def test_a_parent_reports_how_far_it_spreads():
    """Wide spread reads two opposite ways -- a constraint-shaped intent, or
    a cut running across the grain. Which is exactly why it is reported and
    not acted on."""
    parents = {p["id"]: p for p in candidates(_behaviour())["parents"]}
    assert parents["I·01"]["fan_out"] == 3
    assert parents["I·02"]["fan_out"] == 2


def test_candidates_never_look_inside_a_body():
    """Grouping by shared nouns stays the reading agent's job. Token overlap
    here would be unexplainable and would need a tokenizer this unit does
    not have."""
    result = candidates(_behaviour())
    assert "body" not in result["entries"][0]
    assert set(result["entries"][0]) == {"id", "title", "derives_from"}


def test_an_empty_layer_yields_nothing():
    assert candidates(Graph([]))["shared_parentage"] == []


# -- scoring -----------------------------------------------------------------


def test_a_legal_proposal_scores_clean():
    result = score(
        Manifest(project="m"),
        _spec(),
        {"discovery": ["S·01"], "payouts": ["S·02"], "audit": ["S·03"]},
    )
    assert result["legal"] is True
    assert result["slice_findings"] == []


def test_scoring_commits_nothing():
    """The whole point: trial a cut without creating slices, because slices
    split and never merge."""
    manifest = Manifest(project="m")
    score(manifest, _spec(), {"a": ["S·01"], "b": ["S·02"]})
    assert manifest.slices == {}


def test_a_proposal_that_would_cycle_is_reported_before_it_exists():
    """A cycle caught here costs a rename. The same cycle after ratification
    costs a split."""
    graph = Graph(
        [
            Entry(id=parse("S·01"), title="a", depends_on=(parse("S·02"),)),
            Entry(id=parse("S·02"), title="b", depends_on=(parse("S·01"),)),
        ]
    )
    result = score(Manifest(project="m"), graph, {"a": ["S·01"], "b": ["S·02"]})
    assert result["legal"] is False
    assert result["slice_findings"][0]["kind"] == "dependency_cycle"


def test_the_same_entries_cut_differently_score_differently():
    """Putting the coupled pair together removes the cross-slice edge. This
    is what trialling a slicing actually means."""
    graph = Graph(
        [
            Entry(id=parse("S·01"), title="a"),
            Entry(id=parse("S·02"), title="b", depends_on=(parse("S·01"),)),
        ]
    )
    apart = score(Manifest(project="m"), graph, {"a": ["S·01"], "b": ["S·02"]})
    together = score(Manifest(project="m"), graph, {"one": ["S·01", "S·02"]})
    assert apart["slices"][1]["outbound_edges"] == 1
    assert together["slices"][0]["outbound_edges"] == 0
    assert together["slices"][0]["cohesion"] == 1.0


def test_an_entry_in_two_proposed_slices_is_refused():
    with pytest.raises(ValueError, match="exactly one slice"):
        score(Manifest(project="m"), _spec(), {"a": ["S·01"], "b": ["S·01"]})


def test_a_proposal_naming_something_that_does_not_exist_is_refused():
    with pytest.raises(ValueError, match="does not exist"):
        score(Manifest(project="m"), _spec(), {"a": ["S·99"]})


def test_entries_left_out_of_every_slice_are_reported():
    result = score(Manifest(project="m"), _spec(), {"a": ["S·01"]})
    assert result["unassigned"] == ["S·02", "S·03"]
    assert any("nowhere to live" in w for w in result["warnings"])


def test_a_one_entry_slice_is_warned_about_not_refused():
    result = score(Manifest(project="m"), _spec(), {"a": ["S·01"], "b": ["S·02"], "c": ["S·03"]})
    assert result["legal"] is True
    assert any("probably not a slice" in w for w in result["warnings"])


def test_a_chain_is_warned_about():
    graph = Graph(
        [
            Entry(id=parse("S·01"), title="a"),
            Entry(id=parse("S·02"), title="b", depends_on=(parse("S·01"),)),
            Entry(id=parse("S·03"), title="c", depends_on=(parse("S·02"),)),
        ]
    )
    result = score(
        Manifest(project="m"), graph, {"a": ["S·01"], "b": ["S·02"], "c": ["S·03"]}
    )
    assert result["chain"] is True
    assert any("parallel" in w for w in result["warnings"])


def test_a_proposed_cross_cutting_slice_is_checked_as_one():
    """audit reaching back into a feature slice is illegal, and it is caught
    while the classification is still a proposal."""
    graph = Graph(
        [
            Entry(id=parse("S·01"), title="a"),
            Entry(id=parse("S·03"), title="audit", depends_on=(parse("S·01"),)),
        ]
    )
    result = score(
        Manifest(project="m"),
        graph,
        {"a": ["S·01"], "audit": ["S·03"]},
        types={"audit": CROSS_CUTTING},
    )
    assert result["legal"] is False
    assert result["slice_findings"][0]["kind"] == "cross_cutting_outbound"


def test_scoring_returns_inputs_not_a_verdict():
    result = score(Manifest(project="m"), _spec(), {"a": ["S·01", "S·02", "S·03"]})
    assert "verdict" not in result
    assert "good is not" in result["note"]


def test_a_superseded_entry_is_still_a_legitimate_member():
    """Membership is never cleaned up when something is retired. Refusing a
    retired member would make it impossible to score a project's current
    slicing the moment it had taken one correction."""
    graph = Graph(
        [
            Entry(id=parse("S·01"), title="old"),
            Entry(id=parse("S·02"), title="new", supersedes=parse("S·01")),
        ]
    )
    result = score(Manifest(project="m"), graph, {"a": ["S·01", "S·02"]})
    assert result["legal"] is True
    assert result["slices"][0]["size"] == 2
