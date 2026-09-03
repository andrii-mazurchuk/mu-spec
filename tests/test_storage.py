from __future__ import annotations

import pytest

from mu_spec.graph import Entry
from mu_spec.identifiers import parse
from mu_spec.storage import (
    MalformedEntryFile,
    ProjectStore,
    UnknownProject,
    parse_entries,
    render_entries,
)

SAMPLE = """\
## B·14 · A buyer can search listings
derives-from: I·01, I·03

Given a query string, the system returns matching listings
ranked by relevance.

## B·15 · A buyer can filter by price
derives-from: I·01
supersedes: B·09

Filters narrow an existing result set.
"""


# -- the file format --------------------------------------------------------


def test_parses_multiple_entries_from_one_file():
    entries = parse_entries(SAMPLE)
    assert [str(e.id) for e in entries] == ["B·14", "B·15"]


def test_parses_title_from_the_heading():
    assert parse_entries(SAMPLE)[0].title == "A buyer can search listings"


def test_parses_multiple_derives_from_identifiers():
    assert [str(d) for d in parse_entries(SAMPLE)[0].derives_from] == ["I·01", "I·03"]


def test_parses_supersedes_when_present_and_none_when_absent():
    entries = parse_entries(SAMPLE)
    assert entries[0].supersedes is None
    assert str(entries[1].supersedes) == "B·09"


def test_body_excludes_the_heading_and_the_metadata_lines():
    body = parse_entries(SAMPLE)[0].body
    assert body.startswith("Given a query string")
    assert "derives-from" not in body
    assert "B·14" not in body


def test_an_entry_with_no_derives_from_line_parses_with_no_parents():
    """Intent entries derive from nothing, so the line is absent entirely
    rather than present and empty."""
    entries = parse_entries("## I·01 · Buyers can find sellers\n\nThe problem.\n")
    assert entries[0].derives_from == ()


def test_round_trips_through_render():
    entries = parse_entries(SAMPLE)
    assert [
        (str(e.id), e.title, e.derives_from, e.supersedes, e.body.strip())
        for e in parse_entries(render_entries(entries))
    ] == [
        (str(e.id), e.title, e.derives_from, e.supersedes, e.body.strip())
        for e in entries
    ]


def test_content_outside_any_entry_heading_is_ignored():
    """Files carry a human-facing title line at the top. It is not an entry
    and must not become one."""
    text = "# behaviour — listings\n\nNotes for humans.\n\n" + SAMPLE
    assert [str(e.id) for e in parse_entries(text)] == ["B·14", "B·15"]


def test_an_empty_file_yields_no_entries():
    assert parse_entries("") == []


@pytest.mark.parametrize(
    "text",
    [
        "## B·14\n\nno title separator\n",
        "## not-an-identifier · title\n\nbody\n",
        "## B·14 · title\nderives-from: nonsense\n\nbody\n",
        "## B·14 · title\nsupersedes: nonsense\n\nbody\n",
    ],
)
def test_a_malformed_entry_is_a_hard_error(text):
    """Storage never silently drops an entry. A file it cannot parse means
    the graph would come back quietly missing an edge, which is the failure
    this unit exists to prevent."""
    with pytest.raises(MalformedEntryFile):
        parse_entries(text)


# -- the project store ------------------------------------------------------


def test_creating_a_project_lays_out_its_directories(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("marketplace")
    root = tmp_path / "marketplace"
    assert (root / "manifest.json").exists()
    assert (root / "intent.md").exists()
    assert store.list_projects() == ["marketplace"]


def test_creating_a_project_twice_is_an_error(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("marketplace")
    with pytest.raises(FileExistsError):
        store.create_project("marketplace")


def test_reading_an_unknown_project_is_an_error(tmp_path):
    with pytest.raises(UnknownProject):
        ProjectStore(tmp_path).load_graph("nope")


def test_a_new_project_has_an_empty_graph(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("m")
    assert store.load_graph("m").entries() == ()


# -- identifier allocation --------------------------------------------------


def test_allocation_starts_at_one_per_layer(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("m")
    assert str(store.allocate("m", "I")) == "I·01"
    assert str(store.allocate("m", "B")) == "B·01"


def test_allocation_increments_and_persists_across_loads(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.allocate("m", "B")
    store.allocate("m", "B")
    assert str(ProjectStore(tmp_path).allocate("m", "B")) == "B·03"


def test_allocation_never_reuses_an_identifier_even_after_supersession(tmp_path):
    """The high-water mark only ever moves up. Superseding B·01 retires it;
    it does not free the number, because every historical reference to B·01
    must keep meaning what it meant."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    first = store.allocate("m", "B")
    store.append("m", [Entry(id=first, title="original")], slice_name="listings")
    second = store.allocate("m", "B")
    store.append(
        "m",
        [Entry(id=second, title="replacement", supersedes=first)],
        slice_name="listings",
    )
    assert str(store.allocate("m", "B")) == "B·03"


# -- writing and reading back -----------------------------------------------


def test_appended_entries_come_back_from_the_graph(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("I·01"), title="Buyers find sellers")])
    store.append(
        "m",
        [Entry(id=parse("B·01"), derives_from=(parse("I·01"),), title="Search")],
        slice_name="listings",
    )
    graph = store.load_graph("m")
    assert [str(e.id) for e in graph.entries()] == ["I·01", "B·01"]
    assert [str(i) for i in graph.children(parse("I·01"))] == ["B·01"]


def test_intent_lives_in_one_unsliced_file(tmp_path):
    """Intent is short by nature and everyone reads it -- the design doc
    keeps it out of the slice layout deliberately."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("I·01"), title="Buyers find sellers")])
    assert "I·01" in (tmp_path / "m" / "intent.md").read_text(encoding="utf-8")


def test_sliced_layers_write_one_file_per_slice_per_layer(tmp_path):
    """Not one file per entry -- per-file overhead kills you at fifty reads.
    Not one file per layer -- large systems drown the context."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("B·01"), title="a")], slice_name="listings")
    store.append("m", [Entry(id=parse("B·02"), title="b")], slice_name="discovery")
    assert (tmp_path / "m" / "behaviour" / "listings.md").exists()
    assert (tmp_path / "m" / "behaviour" / "discovery.md").exists()


def test_appending_preserves_entries_already_in_the_file(tmp_path):
    """Append-only. A second write to the same slice must not truncate the
    first."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("B·01"), title="a")], slice_name="listings")
    store.append("m", [Entry(id=parse("B·02"), title="b")], slice_name="listings")
    assert [str(e.id) for e in store.load_graph("m").entries()] == ["B·01", "B·02"]


def test_appending_a_duplicate_identifier_is_refused(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("B·01"), title="a")], slice_name="listings")
    with pytest.raises(ValueError):
        store.append("m", [Entry(id=parse("B·01"), title="b")], slice_name="listings")


# -- slice membership -------------------------------------------------------


def test_appending_records_slice_membership_as_a_set(tmp_path):
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append(
        "m",
        [Entry(id=parse("B·01"), title="a"), Entry(id=parse("B·03"), title="c")],
        slice_name="listings",
    )
    manifest = store.load_manifest("m")
    assert manifest.slices["listings"].members == {parse("B·01"), parse("B·03")}


def test_membership_is_not_a_range_so_a_gap_is_perfectly_normal(tmp_path):
    """B·02 belonging to another slice is the ordinary case, not a defect.
    Identifiers encode layer and creation order only, never slice."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("B·01"), title="a")], slice_name="listings")
    store.append("m", [Entry(id=parse("B·02"), title="b")], slice_name="discovery")
    store.append("m", [Entry(id=parse("B·03"), title="c")], slice_name="listings")
    manifest = store.load_manifest("m")
    assert manifest.slice_of(parse("B·02")) == "discovery"
    assert manifest.slices["listings"].members == {parse("B·01"), parse("B·03")}


def test_a_slice_can_split_without_renumbering_anything(tmp_path):
    """The whole reason membership is a set. Splitting moves identifiers
    between membership sets; every entry keeps the identifier it was born
    with, and every historical reference still resolves."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append(
        "m",
        [Entry(id=parse("B·0%d" % n), title=str(n)) for n in (1, 2, 3)],
        slice_name="listings",
    )
    store.split_slice("m", "listings", "media", {parse("B·02")})
    manifest = store.load_manifest("m")
    assert manifest.slices["listings"].members == {parse("B·01"), parse("B·03")}
    assert manifest.slices["media"].members == {parse("B·02")}
    assert [str(e.id) for e in store.load_graph("m").entries()] == [
        "B·01",
        "B·02",
        "B·03",
    ]


def test_slices_cannot_be_merged(tmp_path):
    """Merging destroys identifier locality. There is deliberately no API for
    it, and the split API refuses to target an existing slice."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("B·01"), title="a")], slice_name="listings")
    store.append("m", [Entry(id=parse("B·02"), title="b")], slice_name="discovery")
    with pytest.raises(ValueError):
        store.split_slice("m", "listings", "discovery", {parse("B·01")})
