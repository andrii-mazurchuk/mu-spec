from __future__ import annotations

import json

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

SAMPLE = "\n".join(
    [
        json.dumps(
            {
                "id": "B·14",
                "derives_from": ["I·01", "I·03"],
                "title": "A buyer can search listings",
                "body": "Given a query string, the system returns matching "
                "listings\nranked by relevance.",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "id": "B·15",
                "derives_from": ["I·01"],
                "title": "A buyer can filter by price",
                "body": "Filters narrow an existing result set.",
                "supersedes": "B·09",
            },
            ensure_ascii=False,
        ),
    ]
) + "\n"


# -- the file format --------------------------------------------------------


def test_parses_multiple_entries_from_one_file():
    entries = parse_entries(SAMPLE)
    assert [str(e.id) for e in entries] == ["B·14", "B·15"]


def test_parses_the_title():
    assert parse_entries(SAMPLE)[0].title == "A buyer can search listings"


def test_parses_multiple_derives_from_identifiers():
    assert [str(d) for d in parse_entries(SAMPLE)[0].derives_from] == ["I·01", "I·03"]


def test_parses_supersedes_when_present_and_none_when_absent():
    entries = parse_entries(SAMPLE)
    assert entries[0].supersedes is None
    assert str(entries[1].supersedes) == "B·09"


def test_body_carries_only_the_body():
    body = parse_entries(SAMPLE)[0].body
    assert body.startswith("Given a query string")
    assert "derives_from" not in body
    assert "B·14" not in body


def test_an_entry_with_no_derives_from_parses_with_no_parents():
    """Intent entries derive from nothing, so the field is absent entirely
    rather than present and empty."""
    entries = parse_entries(
        '{"id": "I·01", "title": "Buyers can find sellers", "body": "The problem."}\n'
    )
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


def test_blank_lines_are_skipped():
    """A trailing newline, or padding between records, must not become a
    parse failure."""
    text = "\n" + SAMPLE.replace("\n", "\n\n")
    assert [str(e.id) for e in parse_entries(text)] == ["B·14", "B·15"]


def test_an_empty_file_yields_no_entries():
    assert parse_entries("") == []


@pytest.mark.parametrize(
    "text",
    [
        "not json at all\n",
        '["B·14", "a list, not an object"]\n',
        '{"title": "no id"}\n',
        '{"id": "not-an-identifier"}\n',
        '{"id": "B·14", "derives_from": "I·01"}\n',
        '{"id": "B·14", "derives_from": ["nonsense"]}\n',
        '{"id": "B·14", "supersedes": "nonsense"}\n',
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
    assert (root / "intent.jsonl").exists()
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
    assert "I·01" in (tmp_path / "m" / "intent.jsonl").read_text(encoding="utf-8")


def test_sliced_layers_write_one_file_per_slice_per_layer(tmp_path):
    """Not one file per entry -- per-file overhead kills you at fifty reads.
    Not one file per layer -- large systems drown the context."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("B·01"), title="a")], slice_name="listings")
    store.append("m", [Entry(id=parse("B·02"), title="b")], slice_name="discovery")
    assert (tmp_path / "m" / "behaviour" / "listings.jsonl").exists()
    assert (tmp_path / "m" / "behaviour" / "discovery.jsonl").exists()


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


# -- slice dependency is projected, never authored ---------------------------


def _built(tmp_path):
    """A project where discovery's spec needs something from payouts', and
    nothing declares that anywhere."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append("m", [Entry(id=parse("I·01"), title="intent")])
    store.append(
        "m",
        [Entry(id=parse("S·01"), title="payout ledger")],
        slice_name="payouts",
    )
    store.append(
        "m",
        [
            Entry(
                id=parse("S·02"),
                title="search index",
                depends_on=(parse("S·01"),),
            )
        ],
        slice_name="discovery",
    )
    return store


def test_slice_dependency_is_projected_from_entry_edges(tmp_path):
    store = _built(tmp_path)
    deps = store.load_manifest("m").dependency_graph(store.load_graph("m"))
    assert deps["discovery"] == ("payouts",)
    assert deps["payouts"] == ()


def test_a_dependency_within_one_slice_is_not_a_slice_dependency(tmp_path):
    """A slice depending on itself is just internal structure."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append(
        "m",
        [
            Entry(id=parse("S·01"), title="a"),
            Entry(id=parse("S·02"), title="b", depends_on=(parse("S·01"),)),
        ],
        slice_name="discovery",
    )
    deps = store.load_manifest("m").dependency_graph(store.load_graph("m"))
    assert deps["discovery"] == ()


def test_the_manifest_holds_no_dependency_field_at_all(tmp_path):
    """Not empty -- absent. There is no field to author, so the manifest
    cannot state a dependency that the entries do not have."""
    store = _built(tmp_path)
    raw = json.loads((tmp_path / "m" / "manifest.json").read_text(encoding="utf-8"))
    assert "depends_on" not in raw["slices"]["discovery"]


def test_depends_on_survives_a_write_and_read_round_trip(tmp_path):
    store = _built(tmp_path)
    entry = ProjectStore(tmp_path).load_graph("m").get(parse("S·02"))
    assert [str(d) for d in entry.depends_on] == ["S·01"]
