from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mu_spec.server import UNIT_NAME, handle, read_prompt
from mu_spec.storage import ProjectStore


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "default.md").write_text("# mu-spec\n\nwhat this unit is.\n", encoding="utf-8")
    return d


def call(store, prompts, method, path, body=None):
    status, content_type, raw = handle(
        method, path, store, prompts, body, now_fn=lambda: 1234.5
    )
    parsed = json.loads(raw) if content_type == "application/json" else raw
    return status, parsed


def seed(store, prompts):
    """A project with one intent entry, one behaviour serving it, one
    architecture, and one spec -- a complete vertical column."""
    call(
        store,
        prompts,
        "POST",
        "/projects",
        {"project": "m", "intent": [{"title": "Buyers can find sellers"}]},
    )
    for layer, title, parent in (
        ("B", "A buyer can search listings", "I·01"),
        ("A", "Search runs through an index", "B·01"),
        ("S", "Use the stdlib index module", "A·01"),
    ):
        call(
            store,
            prompts,
            "POST",
            "/projects/m/amendments",
            {
                "slice": "listings",
                "entries": [
                    {
                        "layer": layer,
                        "title": title,
                        "body": f"body for {title}",
                        "derives_from": [parent],
                    }
                ],
            },
        )


# -- the standard unit contract ---------------------------------------------


def test_health_returns_ok(store, prompts):
    assert call(store, prompts, "GET", "/health") == (200, {"status": "ok"})


def test_stats_envelope_is_the_standard_shape(store, prompts):
    seed(store, prompts)
    status, payload = call(store, prompts, "GET", "/stats")
    assert status == 200
    assert payload["unit"] == UNIT_NAME
    assert payload["computed_at"] == 1234.5
    assert payload["metrics"] == {"projects": 1, "entries": 4}


def test_tools_manifest_declares_the_operations(store, prompts):
    status, payload = call(store, prompts, "GET", "/tools")
    names = {t["name"] for t in payload["tools"]}
    assert status == 200
    assert payload["unit"] == UNIT_NAME
    assert {
        "initiate_project",
        "add_feature",
        "report_defect",
        "comment_on_entry",
        "get_work_package",
        "review_layer",
    } <= names


def test_tools_never_declares_health_or_itself(store, prompts):
    _, payload = call(store, prompts, "GET", "/tools")
    paths = {t["path"] for t in payload["tools"]}
    assert "/health" not in paths
    assert "/tools" not in paths


def test_every_declared_tool_path_param_is_a_declared_property(store, prompts):
    """A {name} in a path is substituted from the arguments, so it has to be
    a property the schema actually declares or the call cannot be made."""
    _, payload = call(store, prompts, "GET", "/tools")
    for tool in payload["tools"]:
        for param in re.findall(r"\{(\w+)\}", tool["path"]):
            assert param in tool["input_schema"]["properties"], tool["name"]


def test_prompts_default_tier_returns_raw_text(store, prompts):
    status, body = call(store, prompts, "GET", "/prompts/default")
    assert status == 200
    assert body.startswith("# mu-spec")


def test_missing_prompt_file_404s_rather_than_raising(store, prompts):
    status, _ = call(store, prompts, "GET", "/prompts/reference")
    assert status == 404


def test_unknown_prompt_tier_is_rejected_without_touching_the_filesystem(prompts):
    """Tiers are a closed set. An arbitrary tier must not become an arbitrary
    file read -- the path is never even constructed."""
    assert read_prompt(prompts, "../../etc/passwd") is None
    assert read_prompt(prompts, "made-up") is None


def test_unknown_path_404s(store, prompts):
    assert call(store, prompts, "GET", "/nope")[0] == 404


def test_a_known_path_with_the_wrong_method_is_405_not_404(store, prompts):
    assert call(store, prompts, "POST", "/health")[0] == 405


def test_the_shipped_default_prompt_is_actually_servable(store):
    """prompts/default.md is declared in this unit's manifest entry and
    fetched by peers assembling their own context."""
    status, body = call(store, Path("prompts"), "GET", "/prompts/default")
    assert status == 200
    assert body.strip()


# -- 1. initiate ------------------------------------------------------------


def test_initiate_creates_the_project_and_allocates_intent(store, prompts):
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects",
        {
            "project": "m",
            "intent": [{"title": "Buyers find sellers"}, {"title": "Sellers get paid"}],
        },
    )
    assert status == 201
    assert payload["created"] == ["I·01", "I·02"]
    assert call(store, prompts, "GET", "/projects")[1] == {"projects": ["m"]}


def test_initiate_without_intent_is_refused(store, prompts):
    assert call(store, prompts, "POST", "/projects", {"project": "m"})[0] == 400


def test_initiating_the_same_project_twice_conflicts(store, prompts):
    body = {"project": "m", "intent": [{"title": "x"}]}
    call(store, prompts, "POST", "/projects", body)
    assert call(store, prompts, "POST", "/projects", body)[0] == 409


def test_operations_on_an_unknown_project_404(store, prompts):
    assert call(store, prompts, "GET", "/projects/nope/spine")[0] == 404


# -- 2. add a feature -------------------------------------------------------


def test_add_feature_creates_an_intent_amendment(store, prompts):
    seed(store, prompts)
    status, payload = call(
        store, prompts, "POST", "/projects/m/features", {"title": "Saved searches"}
    )
    assert status == 201
    assert payload["id"] == "I·02"


def test_a_new_feature_shows_up_as_unserved_until_it_is_propagated(store, prompts):
    """The gate is how outstanding work becomes visible. A feature nobody has
    derived behaviour from is a requirement nobody built."""
    seed(store, prompts)
    call(store, prompts, "POST", "/projects/m/features", {"title": "Saved searches"})
    _, gates = call(store, prompts, "GET", "/projects/m/gates")
    assert [(f["kind"], f["id"]) for f in gates["findings"]] == [("unserved", "I·02")]


# -- 3. fix an old one ------------------------------------------------------


def test_report_defect_supersedes_and_reports_the_blast_radius(store, prompts):
    """Fixing the behaviour entry tells you architecture and spec below it now
    have to be re-derived. That list is the entire point."""
    seed(store, prompts)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/defects",
        {
            "layer": "B",
            "title": "A buyer can search listings and saved searches",
            "supersedes": "B·01",
        },
    )
    assert status == 201
    assert payload["id"] == "B·02"
    assert payload["blast_radius"] == ["A·01", "S·01"]


def test_a_replacement_inherits_the_parents_of_what_it_retires(store, prompts):
    seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/defects",
        {"layer": "B", "title": "revised", "supersedes": "B·01"},
    )
    _, entry = call(store, prompts, "GET", "/projects/m/entries/B·02")
    assert entry["derives_from"] == ["I·01"]


def test_a_defect_must_be_classified_to_a_layer(store, prompts):
    seed(store, prompts)
    status, payload = call(
        store, prompts, "POST", "/projects/m/defects", {"title": "something is wrong"}
    )
    assert status == 400
    assert "layer" in payload["error"]


def test_superseding_an_entry_that_does_not_exist_is_refused(store, prompts):
    seed(store, prompts)
    status, _ = call(
        store,
        prompts,
        "POST",
        "/projects/m/defects",
        {"layer": "B", "title": "x", "supersedes": "B·99"},
    )
    assert status == 400


# -- 4. comment -------------------------------------------------------------


def test_a_comment_is_recorded_against_an_entry(store, prompts):
    seed(store, prompts)
    status, _ = call(
        store,
        prompts,
        "POST",
        "/projects/m/comments",
        {"target": "A·01", "body": "why an index rather than a scan?", "author": "a"},
    )
    assert status == 201
    _, listed = call(store, prompts, "GET", "/projects/m/comments?target=A·01")
    assert listed["comments"][0]["body"] == "why an index rather than a scan?"


def test_a_comment_does_not_enter_the_graph(store, prompts):
    """It cannot satisfy a requirement or become a decision -- the spine and
    the gates are unchanged by it."""
    seed(store, prompts)
    _, before = call(store, prompts, "GET", "/projects/m/spine")
    call(
        store,
        prompts,
        "POST",
        "/projects/m/comments",
        {"target": "A·01", "body": "a question"},
    )
    _, after = call(store, prompts, "GET", "/projects/m/spine")
    assert before == after


def test_commenting_on_a_missing_entry_is_refused(store, prompts):
    seed(store, prompts)
    status, _ = call(
        store, prompts, "POST", "/projects/m/comments", {"target": "A·99", "body": "x"}
    )
    assert status == 400


# -- the propagation write path ---------------------------------------------


def test_an_amendment_that_would_orphan_an_entry_is_refused_whole(store, prompts):
    """Atomic: nothing is written, and no identifier is burned."""
    seed(store, prompts)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "entries": [
                {"layer": "A", "title": "good", "derives_from": ["B·01"]},
                {"layer": "A", "title": "bad", "derives_from": ["B·99"]},
            ],
        },
    )
    assert status == 409
    assert payload["admitted"] is False
    _, spine = call(store, prompts, "GET", "/projects/m/spine?layer=A")
    assert [r["id"] for r in spine["spine"]] == ["A·01"]


def test_a_rejected_amendment_does_not_burn_identifiers(store, prompts):
    seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "entries": [{"layer": "A", "title": "bad", "derives_from": ["B·99"]}],
        },
    )
    _, ok = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "entries": [{"layer": "A", "title": "good", "derives_from": ["B·01"]}],
        },
    )
    assert ok["created"] == ["A·02"]


def test_an_amendment_leaving_something_unserved_is_still_admitted(store, prompts):
    """Unserved is the normal state halfway through propagation. Blocking on
    it would make every legitimate amendment illegal."""
    call(
        store,
        prompts,
        "POST",
        "/projects",
        {"project": "m", "intent": [{"title": "Buyers find sellers"}]},
    )
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "entries": [{"layer": "B", "title": "search", "derives_from": ["I·01"]}],
        },
    )
    assert status == 200
    assert payload["admitted"] is True
    assert [f["id"] for f in payload["gates"]["findings"]] == ["B·01"]


# -- 5. retrieve the final layer --------------------------------------------


def test_work_package_carries_the_write_set_with_full_bodies(store, prompts):
    seed(store, prompts)
    status, payload = call(
        store, prompts, "GET", "/projects/m/work-package?slice=listings"
    )
    assert status == 200
    assert payload["issued"] is True
    assert [e["id"] for e in payload["write_set"]] == ["S·01"]
    assert payload["write_set"][0]["body"]


def test_work_package_justification_is_full_for_the_parent_and_spine_above(
    store, prompts
):
    """The executor needs the architectural decision in full, and merely needs
    to know the behaviour and intent above it exist."""
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/work-package?slice=listings")
    chain = {e["id"]: e for e in payload["justification"]["S·01"]}
    assert "body" in chain["A·01"]
    assert "body" not in chain["B·01"]
    assert "body" not in chain["I·01"]


def test_work_package_declares_what_may_be_edited(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/work-package?slice=listings")
    assert payload["audit"]["editable_ids"] == ["S·01"]


def test_work_package_is_refused_when_the_graph_is_unsound(store, prompts):
    """The realistic path to unsound: fixing a behaviour retires B·01, and
    A·01 is left pointing at a retired entry. The architecture is now a stale
    reference, so no code may be produced from the spec beneath it until it
    is re-derived. Handing an executor a package built from a broken chain
    produces code derived from a lie, and the failure surfaces much later."""
    seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/defects",
        {"layer": "B", "title": "revised behaviour", "supersedes": "B·01"},
    )
    status, payload = call(
        store, prompts, "GET", "/projects/m/work-package?slice=listings"
    )
    assert status == 409
    assert payload["issued"] is False
    assert payload["gates"]["sound"] is False
    assert [f["id"] for f in payload["gates"]["findings"] if f["kind"] == "orphan"] == [
        "A·01"
    ]


def test_work_package_is_still_issued_while_another_branch_is_incomplete(
    store, prompts
):
    """Incompleteness elsewhere says nothing about whether this slice is
    ready. Blocking on it would mean no slice could ship until every slice
    was finished."""
    seed(store, prompts)
    call(store, prompts, "POST", "/projects/m/features", {"title": "not yet built"})
    status, payload = call(
        store, prompts, "GET", "/projects/m/work-package?slice=listings"
    )
    assert status == 200
    assert payload["issued"] is True


def test_work_package_needs_a_slice(store, prompts):
    seed(store, prompts)
    assert call(store, prompts, "GET", "/projects/m/work-package")[0] == 400


def test_work_package_for_an_unknown_slice_is_refused(store, prompts):
    seed(store, prompts)
    assert call(store, prompts, "GET", "/projects/m/work-package?slice=nope")[0] == 400


# -- 6. review --------------------------------------------------------------


def test_review_shows_entries_with_their_justification_and_what_serves_them(
    store, prompts
):
    seed(store, prompts)
    status, payload = call(store, prompts, "GET", "/projects/m/review?layer=A")
    row = payload["entries"][0]
    assert status == 200
    assert row["id"] == "A·01"
    assert row["derives_from_titles"] == [
        {"id": "B·01", "title": "A buyer can search listings"}
    ]
    assert row["served_by"] == ["S·01"]


def test_review_attaches_comments_to_the_entry_they_target(store, prompts):
    seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/comments",
        {"target": "A·01", "body": "why an index?"},
    )
    _, payload = call(store, prompts, "GET", "/projects/m/review?layer=A")
    assert payload["entries"][0]["comments"][0]["body"] == "why an index?"


def test_review_needs_a_layer(store, prompts):
    seed(store, prompts)
    assert call(store, prompts, "GET", "/projects/m/review")[0] == 400


# -- retrieval primitives ---------------------------------------------------


def test_spine_carries_no_bodies(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/spine")
    assert [r["id"] for r in payload["spine"]] == ["I·01", "B·01", "A·01", "S·01"]
    assert all("body" not in row for row in payload["spine"])


def test_spine_reports_which_slice_each_entry_belongs_to(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/spine")
    slices = {r["id"]: r["slice"] for r in payload["spine"]}
    assert slices["S·01"] == "listings"
    assert slices["I·01"] is None  # intent is not sliced


def test_spine_can_be_filtered_to_one_layer(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/spine?layer=S")
    assert [r["id"] for r in payload["spine"]] == ["S·01"]


def test_get_entry_returns_the_body_and_both_directions(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/entries/A·01")
    assert payload["derives_from"] == ["B·01"]
    assert payload["children"] == ["S·01"]
    assert payload["body"]


def test_get_entry_for_a_missing_identifier_is_refused(store, prompts):
    seed(store, prompts)
    assert call(store, prompts, "GET", "/projects/m/entries/A·99")[0] == 400
