from __future__ import annotations

import pytest

from mu_spec.graph import DuplicateIdentifier, Entry, Graph
from mu_spec.identifiers import parse


def _entry(ident: str, derives_from: str = "", body: str | None = None, **kw) -> Entry:
    # `body is None` means "give me a default"; an explicit "" is a real,
    # deliberately empty body and must survive as one.
    return Entry(
        id=parse(ident),
        derives_from=tuple(parse(d) for d in derives_from.split() if d),
        body=f"# {ident} title\n\nbody of {ident}.\n" if body is None else body,
        **kw,
    )


def _chain() -> Graph:
    """A minimal well-formed graph: one intent, one behaviour serving it, one
    architecture serving that, two spec entries serving the architecture."""
    return Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _entry("A·01", "B·01"),
            _entry("S·01", "A·01"),
            _entry("S·02", "A·01"),
        ]
    )


# -- construction and structural invariants ---------------------------------


def test_rejects_duplicate_identifiers():
    """Identifiers are never reused. A duplicate is a hard error, not a
    last-one-wins merge -- silently dropping an entry loses whatever derived
    from it."""
    with pytest.raises(DuplicateIdentifier):
        Graph([_entry("B·01", "I·01"), _entry("B·01", "I·01")])


def test_entries_are_returned_in_spine_order():
    ids = [str(e.id) for e in _chain().entries()]
    assert ids == ["I·01", "B·01", "A·01", "S·01", "S·02"]


def test_lookup_by_identifier():
    graph = _chain()
    assert graph.get(parse("B·01")).id == parse("B·01")
    assert graph.get(parse("B·99")) is None


# -- edges ------------------------------------------------------------------


def test_parents_are_what_an_entry_derives_from():
    assert [str(i) for i in _chain().parents(parse("S·01"))] == ["A·01"]


def test_children_are_what_derives_from_an_entry():
    assert [str(i) for i in _chain().children(parse("A·01"))] == ["S·01", "S·02"]


def test_ancestors_walk_all_the_way_to_intent():
    assert [str(i) for i in _chain().ancestors(parse("S·02"))] == [
        "I·01",
        "B·01",
        "A·01",
    ]


def test_descendants_walk_all_the_way_down():
    assert [str(i) for i in _chain().descendants(parse("I·01"))] == [
        "B·01",
        "A·01",
        "S·01",
        "S·02",
    ]


def test_edges_to_unknown_identifiers_do_not_raise_during_traversal():
    """A dangling edge is a gate finding, not a crash. Traversal has to keep
    working on a graph that is mid-edit, or the gate could never report."""
    graph = Graph([_entry("B·01", "I·99")])
    assert graph.ancestors(parse("B·01")) == ()


def test_traversal_terminates_on_a_cycle():
    """Layer direction makes a cycle impossible in a valid graph, but an
    invalid one must still be inspectable rather than hanging the process."""
    graph = Graph([_entry("B·01", "B·02"), _entry("B·02", "B·01")])
    assert [str(i) for i in graph.ancestors(parse("B·01"))] == ["B·01", "B·02"]


# -- blast radius -----------------------------------------------------------


def test_blast_radius_is_the_downstream_closure():
    """The point of the whole graph: what does changing this touch?"""
    assert [str(i) for i in _chain().blast_radius([parse("B·01")])] == [
        "A·01",
        "S·01",
        "S·02",
    ]


def test_blast_radius_of_several_entries_is_deduplicated():
    """S·01 and S·02 are downstream of both changed entries and appear once.
    A·01 is downstream of B·01 but is itself in the changed set, so the
    exclusion rule above takes precedence over the inclusion."""
    radius = _chain().blast_radius([parse("A·01"), parse("B·01")])
    assert [str(i) for i in radius] == ["S·01", "S·02"]


def test_blast_radius_excludes_the_changed_entries_themselves():
    """Callers already know what they changed. Including it makes the count
    misleading when reporting 'this touches N entries'."""
    assert parse("S·01") not in _chain().blast_radius([parse("S·01")])


# -- superseding ------------------------------------------------------------


def test_a_superseded_entry_is_excluded_from_the_live_graph():
    """Amendments are append-only: the old entry stays in the file, marked,
    and stops participating."""
    graph = Graph(
        [
            _entry("I·01"),
            _entry("B·01", "I·01"),
            _entry("B·02", "I·01", supersedes=parse("B·01")),
        ]
    )
    assert [str(e.id) for e in graph.entries()] == ["I·01", "B·02"]
    assert [str(i) for i in graph.children(parse("I·01"))] == ["B·02"]


def test_superseded_entries_remain_retrievable_by_identifier():
    """History is what makes the pipeline auditable. Excluded from the live
    graph is not the same as deleted."""
    graph = Graph(
        [_entry("B·01", "I·01"), _entry("B·02", "I·01", supersedes=parse("B·01"))]
    )
    assert graph.get(parse("B·01")) is not None
    assert graph.superseded_by(parse("B·01")) == parse("B·02")


# -- spine ------------------------------------------------------------------


def test_spine_carries_identifier_title_and_edges_only():
    """Roughly fifteen tokens an entry: the agent loads spines
    unconditionally, then pulls bodies by identifier once it knows which it
    needs. A spine carrying bodies would defeat the entire scheme."""
    graph = Graph([_entry("I·01", body="# Sellers list items\n\nlong body...\n")])
    assert graph.spine() == [("I·01", "Sellers list items", (), ())]


def test_spine_title_is_the_first_line_stripped_of_heading_marks():
    graph = Graph([_entry("B·01", "I·01", body="## A buyer can search\n\nmore\n")])
    assert graph.spine()[0][1] == "A buyer can search"


def test_spine_title_of_an_empty_body_is_empty_not_an_error():
    graph = Graph([_entry("B·01", "I·01", body="")])
    assert graph.spine()[0][1] == ""


def test_spine_records_derives_from():
    graph = Graph([_entry("I·01"), _entry("B·01", "I·01")])
    assert graph.spine()[1] == ("B·01", "B·01 title", ("I·01",), ())


# -- same-layer dependency edges --------------------------------------------


def test_an_entry_can_declare_same_layer_dependencies():
    """The horizontal edge. `derives_from` says what an entry serves one
    layer up; `depends_on` says what it needs from its own layer."""
    graph = Graph(
        [
            _entry("A·01"),
            Entry(id=parse("A·02"), depends_on=(parse("A·01"),), title="b"),
        ]
    )
    assert [str(i) for i in graph.dependencies(parse("A·02"))] == ["A·01"]
    assert [str(i) for i in graph.dependents(parse("A·01"))] == ["A·02"]


def test_dependencies_of_an_unknown_entry_are_empty():
    assert Graph([]).dependencies(parse("A·01")) == ()
    assert Graph([]).dependents(parse("A·01")) == ()


def test_a_superseded_entry_does_not_depend_on_anything_live():
    """Retired entries leave the live graph entirely, horizontally as well as
    vertically -- otherwise a replaced entry keeps casting dependency votes."""
    graph = Graph(
        [
            _entry("A·01"),
            Entry(id=parse("A·02"), depends_on=(parse("A·01"),), title="old"),
            Entry(id=parse("A·03"), title="new", supersedes=parse("A·02")),
        ]
    )
    assert graph.dependents(parse("A·01")) == ()


def test_the_spine_carries_both_edge_kinds():
    """An agent decides what to load from the spine alone. If the horizontal
    edges were only visible in the bodies, it would have to load bodies to
    find out which bodies it needs."""
    graph = Graph(
        [
            _entry("B·01"),
            Entry(
                id=parse("A·01"),
                derives_from=(parse("B·01"),),
                depends_on=(parse("A·02"),),
                title="a",
            ),
            _entry("A·02", "B·01"),
        ]
    )
    row = [r for r in graph.spine() if r[0] == "A·01"][0]
    assert row[2] == ("B·01",)
    assert row[3] == ("A·02",)
