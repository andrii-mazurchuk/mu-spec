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


def post(store, prompts, kind, title, project="m", **kw):
    """Ask for something. The only way in from outside."""
    return call(
        store,
        prompts,
        "POST",
        "/inbox",
        {"type": kind, "title": title, "project": project, **kw},
    )


def seed(store, prompts):
    """A project with one intent entry, one behaviour serving it, one
    architecture, and one spec -- a complete vertical column, built the way
    the pipeline actually builds it: a request, then amendments citing it."""
    _, msg = post(store, prompts, "initiate", "a marketplace", project="m")
    mid = msg["message_id"]
    call(store, prompts, "POST", "/projects", {"project": "m", "in_response_to": mid})
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "in_response_to": mid,
            "entries": [{"layer": "I", "title": "Buyers can find sellers"}],
        },
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
                "in_response_to": mid,
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
    return mid


# -- the standard unit contract ---------------------------------------------


def test_health_returns_ok(store, prompts):
    assert call(store, prompts, "GET", "/health") == (200, {"status": "ok"})


def test_stats_envelope_is_the_standard_shape(store, prompts):
    seed(store, prompts)
    status, payload = call(store, prompts, "GET", "/stats")
    assert status == 200
    assert payload["unit"] == UNIT_NAME
    assert payload["computed_at"] == 1234.5
    metrics = payload["metrics"]
    assert metrics["projects"] == 1
    assert metrics["entries"] == 4
    assert metrics["entries_by_layer"] == {
        "architecture": 1,
        "behaviour": 1,
        "intent": 1,
        "spec": 1,
    }
    # Already-processed, per the standard: an analytical unit reads these
    # without knowing anything about how this unit works.
    assert metrics["change_locality_mean"] == 1.0
    assert metrics["corrections_by_layer"] == {}
    assert "unimplemented_spec" in metrics
    # No text and no per-item detail -- that is what logs are for.
    assert not any(isinstance(v, list) for v in metrics.values())


def test_tools_manifest_declares_the_operations(store, prompts):
    status, payload = call(store, prompts, "GET", "/tools")
    names = {t["name"] for t in payload["tools"]}
    assert status == 200
    assert payload["unit"] == UNIT_NAME
    assert {
        "post_request",
        "list_requests",
        "resolve_request",
        "create_project",
        "submit_amendment",
        "classify_slice",
        "get_waves",
        "raise_issue",
        "list_issues",
        "close_issue",
        "get_reconciliation",
        "propose_slicing",
        "score_slicing",
        "get_insights",
        "list_events",
        "declare_module",
        "list_modules",
        "get_plan",
        "audit_diff",
        "get_work_package",
        "review_layer",
    } <= names
    # No tool writes an entry directly, and none names a layer.
    assert not any("defect" in n or "feature" in n for n in names)


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
    """The fixture ships only a default tier."""
    status, _ = call(store, prompts, "GET", "/prompts/reference")
    assert status == 404


def test_the_shipped_reference_tier_carries_the_intake_interview(store):
    """The interview skill is served through the standard prompts mechanism,
    so a finished version goes live without any new endpoint. Declared in
    this unit's manifest entry and linked from the default prompt."""
    status, body = call(store, Path("prompts"), "GET", "/prompts/reference")
    assert status == 200
    assert "intent entries" in body


def test_the_default_prompt_points_at_the_reference_tier(store):
    _, body = call(store, Path("prompts"), "GET", "/prompts/default")
    assert "/prompts/reference" in body


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


# -- the inbox: the single external door ------------------------------------


def test_a_request_records_what_someone_wants_and_changes_nothing(store, prompts):
    seed(store, prompts)
    _, before = call(store, prompts, "GET", "/projects/m/spine")
    status, payload = call(
        store,
        prompts,
        "POST",
        "/inbox",
        {"type": "feature", "project": "m", "title": "Saved searches"},
    )
    assert status == 201
    assert payload["status"] == "pending"
    _, after = call(store, prompts, "GET", "/projects/m/spine")
    assert before == after


def test_a_request_needs_a_known_type(store, prompts):
    seed(store, prompts)
    status, payload = call(
        store, prompts, "POST", "/inbox", {"type": "patch_the_spec", "title": "x"}
    )
    assert status == 400
    assert "type" in payload["error"]


def test_targets_are_optional_because_the_asker_cannot_know_the_layout(store, prompts):
    """Requiring a target would push this unit's internal structure onto the
    outside world, which is the coupling the whole design avoids."""
    status, _ = call(
        store,
        prompts,
        "POST",
        "/inbox",
        {"type": "feature", "project": "m", "title": "something, somewhere"},
    )
    assert status == 201


def test_a_target_is_accepted_as_a_hint_when_the_asker_does_know(store, prompts):
    seed(store, prompts)
    _, payload = call(
        store,
        prompts,
        "POST",
        "/inbox",
        {
            "type": "comment",
            "project": "m",
            "title": "why an index?",
            "targets": ["A·01"],
        },
    )
    _, msg = call(store, prompts, "GET", f"/inbox/{payload['message_id']}")
    assert msg["targets"] == ["A·01"]


def test_the_queue_can_be_filtered_by_status(store, prompts):
    seed(store, prompts)
    post(store, prompts, "feature", "one")
    post(store, prompts, "feature", "two")
    _, payload = call(store, prompts, "GET", "/inbox?status=pending&project=m")
    assert [m["title"] for m in payload["messages"]] == ["a marketplace", "one", "two"]


def test_a_request_is_resolved_with_what_it_produced(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "feature", "Saved searches")
    status, payload = call(
        store,
        prompts,
        "POST",
        f"/inbox/{msg['message_id']}/resolve",
        {"status": "accepted", "note": "shipped", "produced": ["I·02"]},
    )
    assert status == 200
    assert payload["status"] == "accepted"
    assert payload["resolution"]["produced"] == ["I·02"]


def test_a_request_cannot_be_resolved_twice(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "feature", "Saved searches")
    call(store, prompts, "POST", f"/inbox/{msg['message_id']}/resolve", {})
    status, _ = call(store, prompts, "POST", f"/inbox/{msg['message_id']}/resolve", {})
    assert status == 400


# -- authority: every write cites the request that asked for it -------------


def test_an_amendment_must_cite_a_request(store, prompts):
    """An amendment nobody asked for is refused. This is the mechanical brake
    on an agent quietly adding scope."""
    seed(store, prompts)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {"entries": [{"layer": "I", "title": "scope I invented"}]},
    )
    assert status == 400
    assert "in_response_to" in payload["error"]


def test_an_amendment_citing_an_unknown_request_is_refused(store, prompts):
    seed(store, prompts)
    status, _ = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {"in_response_to": "msg-9999", "entries": [{"layer": "I", "title": "x"}]},
    )
    assert status == 400


def test_a_project_can_only_be_created_from_an_initiate_request(store, prompts):
    _, msg = call(
        store,
        prompts,
        "POST",
        "/inbox",
        {"type": "feature", "project": "m", "title": "not an initiate"},
    )
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects",
        {"project": "m", "in_response_to": msg["message_id"]},
    )
    assert status == 400
    assert "initiate" in payload["error"]


# -- origination depth: how deep a request may reach ------------------------


def test_a_feature_may_originate_at_intent(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "feature", "Saved searches")
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "in_response_to": msg["message_id"],
            "entries": [{"layer": "I", "title": "Buyers can save a search"}],
        },
    )
    assert status == 200
    assert payload["created"] == ["I·02"]


def test_a_feature_may_not_originate_at_behaviour(store, prompts):
    """A new capability is a new requirement. Starting at behaviour would
    leave intent silent about something the product now does."""
    seed(store, prompts)
    _, msg = post(store, prompts, "feature", "Saved searches")
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": msg["message_id"],
            "entries": [
                {"layer": "B", "title": "sneaking in", "derives_from": ["I·01"]}
            ],
        },
    )
    assert status == 400
    assert "originate" in payload["error"]


def test_a_correction_may_originate_at_behaviour(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "correction", "ranking is wrong")
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": msg["message_id"],
            "entries": [
                {
                    "layer": "B",
                    "title": "search results are ranked by rating",
                    "derives_from": ["I·01"],
                    "supersedes": "B·01",
                }
            ],
        },
    )
    assert status == 200
    assert payload["created"] == ["B·02"]


def test_a_correction_may_not_originate_at_spec(store, prompts):
    """The hole this closes: patching the spec while intent, behaviour and
    architecture still say the old thing is how the artifacts start lying."""
    seed(store, prompts)
    _, msg = post(store, prompts, "correction", "just change the module name")
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": msg["message_id"],
            "entries": [
                {
                    "layer": "S",
                    "title": "use a different module",
                    "derives_from": ["A·01"],
                    "supersedes": "S·01",
                }
            ],
        },
    )
    assert status == 400
    assert "spec" in payload["error"]


def test_a_comment_may_never_create_entries(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "comment", "an observation", targets=["A·01"])
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "in_response_to": msg["message_id"],
            "entries": [{"layer": "I", "title": "smuggled in as a comment"}],
        },
    )
    assert status == 400
    assert "never creates entries" in payload["error"]


def test_propagation_below_the_origin_is_unrestricted(store, prompts):
    """The restriction is on where a change may enter, not how far it may
    travel. Once a feature has originated at intent, carrying it down to
    behaviour, architecture and spec is the pipeline doing its job."""
    seed(store, prompts)
    _, msg = post(store, prompts, "feature", "Saved searches")
    mid = msg["message_id"]
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {"in_response_to": mid, "entries": [{"layer": "I", "title": "save searches"}]},
    )
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "B",
                    "title": "a buyer saves a search",
                    "derives_from": ["I·02"],
                }
            ],
        },
    )
    assert status == 200
    assert payload["created"] == ["B·02"]


def test_amendments_record_what_they_produced_against_the_request(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "feature", "Saved searches")
    mid = msg["message_id"]
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {"in_response_to": mid, "entries": [{"layer": "I", "title": "save searches"}]},
    )
    _, message = call(store, prompts, "GET", f"/inbox/{mid}")
    assert message["resolution"]["produced"] == ["I·02"]


# -- the propagation write path ---------------------------------------------


def test_an_amendment_that_would_orphan_an_entry_is_refused_whole(store, prompts):
    """Atomic: nothing is written, and no identifier is burned."""
    mid = seed(store, prompts)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
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
    mid = seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
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
            "in_response_to": mid,
            "entries": [{"layer": "A", "title": "good", "derives_from": ["B·01"]}],
        },
    )
    assert ok["created"] == ["A·02"]


def test_an_amendment_leaving_something_unserved_is_still_admitted(store, prompts):
    """Unserved is the normal state halfway through propagation. Blocking on
    it would make every legitimate amendment illegal."""
    _, msg = call(
        store,
        prompts,
        "POST",
        "/inbox",
        {"type": "initiate", "project": "m", "title": "a marketplace"},
    )
    mid = msg["message_id"]
    call(store, prompts, "POST", "/projects", {"project": "m", "in_response_to": mid})
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {"in_response_to": mid, "entries": [{"layer": "I", "title": "find sellers"}]},
    )
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {"layer": "B", "title": "search", "derives_from": ["I·01"]}
            ],
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
    """The realistic path to unsound: a correction retires B·01, and A·01
    is left pointing at a retired entry. The architecture is now a stale
    reference, so no code may be produced from the spec beneath it until it
    is re-derived."""
    seed(store, prompts)
    _, msg = post(store, prompts, "correction", "ranking is wrong")
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": msg["message_id"],
            "entries": [
                {
                    "layer": "B",
                    "title": "ranked by rating",
                    "derives_from": ["I·01"],
                    "supersedes": "B·01",
                }
            ],
        },
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
    _, msg = post(store, prompts, "feature", "not yet built")
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "in_response_to": msg["message_id"],
            "entries": [{"layer": "I", "title": "later"}],
        },
    )
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
    post(store, prompts, "comment", "why an index?", targets=["A·01"])
    _, payload = call(store, prompts, "GET", "/projects/m/review?layer=A")
    assert payload["entries"][0]["comments"][0]["title"] == "why an index?"


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


# -- same-layer dependencies, end to end -------------------------------------


def _second_slice(store, prompts, mid, depends_on=None):
    """A second vertical column, whose spec optionally needs something from
    the first slice's spec."""
    for layer, title, parent in (
        ("B", "A seller is paid out", "I·01"),
        ("A", "Payouts run nightly", "B·02"),
        ("S", "Use the ledger module", "A·02"),
    ):
        entry = {
            "layer": layer,
            "title": title,
            "body": f"body for {title}",
            "derives_from": [parent],
        }
        if layer == "S" and depends_on:
            entry["depends_on"] = depends_on
        call(
            store,
            prompts,
            "POST",
            "/projects/m/amendments",
            {"slice": "payouts", "in_response_to": mid, "entries": [entry]},
        )


def test_the_spine_shows_both_edge_kinds(store, prompts):
    mid = seed(store, prompts)
    _second_slice(store, prompts, mid, depends_on=["S·01"])
    _, payload = call(store, prompts, "GET", "/projects/m/spine?layer=S")
    rows = {r["id"]: r for r in payload["spine"]}
    assert rows["S·02"]["derives_from"] == ["A·02"]
    assert rows["S·02"]["depends_on"] == ["S·01"]


def test_the_read_set_follows_a_dependency_nobody_declared(store, prompts):
    """The manifest has no field to declare a slice dependency in. payouts
    lands in discovery's read set purely because an entry said so."""
    mid = seed(store, prompts)
    _second_slice(store, prompts, mid, depends_on=["S·01"])
    _, wp = call(store, prompts, "GET", "/projects/m/work-package?slice=payouts")
    assert wp["issued"] is True
    assert [(e["id"], e["slice"]) for e in wp["read_set"]] == [("S·01", "listings")]
    assert "body" not in wp["read_set"][0]


def test_a_slice_with_no_dependencies_gets_an_empty_read_set(store, prompts):
    mid = seed(store, prompts)
    _second_slice(store, prompts, mid)
    _, wp = call(store, prompts, "GET", "/projects/m/work-package?slice=payouts")
    assert wp["read_set"] == []


def test_an_amendment_depending_across_layers_is_refused(store, prompts):
    """depends_on is horizontal. A cross-layer edge is a derivation wearing
    the wrong label, and the orphan gate would never see it."""
    mid = seed(store, prompts)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "payouts",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "B",
                    "title": "A seller is paid out",
                    "derives_from": ["I·01"],
                    "depends_on": ["A·01"],
                }
            ],
        },
    )
    assert payload["admitted"] is False
    assert payload["findings"][0]["kind"] == "bad_dependency"
    assert "same layer" in payload["findings"][0]["detail"]


def test_an_amendment_depending_on_nothing_that_exists_is_refused(store, prompts):
    mid = seed(store, prompts)
    _, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "payouts",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "B",
                    "title": "A seller is paid out",
                    "derives_from": ["I·01"],
                    "depends_on": ["B·99"],
                }
            ],
        },
    )
    assert payload["admitted"] is False
    assert payload["findings"][0]["kind"] == "bad_dependency"


def test_no_work_package_is_issued_while_a_dependency_is_stale(store, prompts):
    """Superseding S·01 leaves payouts holding the old meaning. The graph is
    unsound until payouts re-derives, and no executor is handed a package
    built on it."""
    mid = seed(store, prompts)
    _second_slice(store, prompts, mid, depends_on=["S·01"])
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "Use a different index module",
                    "derives_from": ["A·01"],
                    "supersedes": "S·01",
                }
            ],
        },
    )
    _, wp = call(store, prompts, "GET", "/projects/m/work-package?slice=payouts")
    assert wp["issued"] is False
    kinds = {f["kind"] for f in wp["gates"]["findings"]}
    assert "bad_dependency" in kinds


# -- cross-cutting slices ----------------------------------------------------


def test_cross_cutting_entries_arrive_undeclared(store, prompts):
    """Nothing in listings depends on audit. It lands in the work package
    anyway -- that is the entire operational difference between a
    cross-cutting slice and an ordinary one."""
    mid = seed(store, prompts)
    for layer, title, parent in (
        ("B", "Every state change is recorded", "I·01"),
        ("A", "An append-only event log", "B·02"),
        ("S", "audit/log.py appends a record", "A·02"),
    ):
        call(
            store,
            prompts,
            "POST",
            "/projects/m/amendments",
            {
                "slice": "audit",
                "in_response_to": mid,
                "entries": [
                    {"layer": layer, "title": title, "derives_from": [parent]}
                ],
            },
        )
    status, ruling = call(
        store, prompts, "POST", "/projects/m/slices/audit/type",
        {"type": "cross_cutting"},
    )
    assert (status, ruling["cross_cutting"]) == (200, ["audit"])

    _, wp = call(store, prompts, "GET", "/projects/m/work-package?slice=listings")
    assert wp["read_set"] == []
    assert [(e["id"], e["slice"]) for e in wp["cross_cutting"]] == [("S·02", "audit")]
    assert "body" not in wp["cross_cutting"][0]


def test_a_cross_cutting_slice_does_not_read_itself(store, prompts):
    mid = seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "audit",
            "in_response_to": mid,
            "entries": [
                {"layer": "B", "title": "Every state change is recorded",
                 "derives_from": ["I·01"]},
            ],
        },
    )
    call(
        store, prompts, "POST", "/projects/m/slices/audit/type",
        {"type": "cross_cutting"},
    )
    _, wp = call(store, prompts, "GET", "/projects/m/work-package?slice=audit")
    assert wp["cross_cutting"] == []


def test_several_slices_can_be_cross_cutting_at_once(store, prompts):
    """A type, not a reserved name -- so audit and telemetry are two separate
    columns, both ambient."""
    mid = seed(store, prompts)
    for name in ("audit", "telemetry"):
        call(
            store,
            prompts,
            "POST",
            "/projects/m/amendments",
            {
                "slice": name,
                "in_response_to": mid,
                "entries": [
                    {"layer": "B", "title": f"{name} everywhere",
                     "derives_from": ["I·01"]}
                ],
            },
        )
        call(
            store, prompts, "POST", f"/projects/m/slices/{name}/type",
            {"type": "cross_cutting"},
        )
    assert store.load_manifest("m").cross_cutting() == ("audit", "telemetry")


def test_an_unknown_slice_type_is_a_caller_error(store, prompts):
    seed(store, prompts)
    status, payload = call(
        store, prompts, "POST", "/projects/m/slices/listings/type",
        {"type": "sort-of-ambient"},
    )
    assert status == 400
    assert "unknown slice type" in payload["error"]


# -- slice-level gates, end to end -------------------------------------------


def _spec_in(store, prompts, mid, slice_name, title, parent, depends_on=None):
    entry = {"layer": "S", "title": title, "derives_from": [parent]}
    if depends_on:
        entry["depends_on"] = depends_on
    return call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {"slice": slice_name, "in_response_to": mid, "entries": [entry]},
    )


def _two_columns(store, prompts):
    """listings (S·01) and payouts (S·02), no dependency between them yet."""
    mid = seed(store, prompts)
    for layer, title, parent in (
        ("B", "A seller is paid out", "I·01"),
        ("A", "Payouts run nightly", "B·02"),
        ("S", "Use the ledger module", "A·02"),
    ):
        call(
            store,
            prompts,
            "POST",
            "/projects/m/amendments",
            {
                "slice": "payouts",
                "in_response_to": mid,
                "entries": [
                    {"layer": layer, "title": title, "derives_from": [parent]}
                ],
            },
        )
    return mid


def test_an_amendment_closing_a_slice_cycle_is_refused(store, prompts):
    """payouts already depends on listings. A listings entry that depends
    back on payouts would leave neither derivable first."""
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    status, payload = _spec_in(
        store, prompts, mid, "listings", "index reads the ledger", "A·01",
        depends_on=["S·02"],
    )
    assert (status, payload["admitted"]) == (409, False)
    finding = payload["slice_findings"][0]
    assert finding["kind"] == "dependency_cycle"
    assert "listings -> payouts -> listings" in finding["detail"]
    assert "pull the shared part out" in finding["detail"]


def test_a_one_way_dependency_between_slices_is_admitted(store, prompts):
    mid = _two_columns(store, prompts)
    status, payload = _spec_in(
        store, prompts, mid, "payouts", "ledger reads the index", "A·02",
        depends_on=["S·01"],
    )
    assert (status, payload["admitted"]) == (200, True)
    assert payload["gates"]["slice_findings"] == []


def test_a_cross_cutting_slice_reaching_into_a_feature_slice_is_refused(store, prompts):
    mid = seed(store, prompts)
    for layer, title, parent in (
        ("B", "Every state change is recorded", "I·01"),
        ("A", "An append-only event log", "B·02"),
    ):
        call(
            store,
            prompts,
            "POST",
            "/projects/m/amendments",
            {
                "slice": "audit",
                "in_response_to": mid,
                "entries": [
                    {"layer": layer, "title": title, "derives_from": [parent]}
                ],
            },
        )
    call(store, prompts, "POST", "/projects/m/slices/audit/type",
         {"type": "cross_cutting"})
    status, payload = _spec_in(
        store, prompts, mid, "audit", "audit/log.py reads the listing", "A·02",
        depends_on=["S·01"],
    )
    assert (status, payload["admitted"]) == (409, False)
    assert payload["slice_findings"][0]["kind"] == "cross_cutting_outbound"


def test_classifying_a_slice_that_already_reaches_out_is_refused(store, prompts):
    """The same rule, through the other door. payouts depends on listings, so
    it cannot be declared cross-cutting after the fact."""
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    status, payload = call(
        store, prompts, "POST", "/projects/m/slices/payouts/type",
        {"type": "cross_cutting"},
    )
    assert status == 409
    assert payload["slice_findings"][0]["kind"] == "cross_cutting_outbound"
    assert store.load_manifest("m").cross_cutting() == ()


def test_no_work_package_is_issued_while_slices_cycle(store, prompts):
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    # Build the cycle behind the gate's back, the way a bad import would.
    from mu_spec.graph import Entry
    from mu_spec.identifiers import parse as pid

    store.append(
        "m",
        [Entry(id=pid("S·09"), derives_from=(pid("A·01"),), title="back-edge",
               depends_on=(pid("S·02"),))],
        slice_name="listings",
    )
    _, wp = call(store, prompts, "GET", "/projects/m/work-package?slice=listings")
    assert wp["issued"] is False
    assert wp["gates"]["slice_findings"][0]["kind"] == "dependency_cycle"


# -- emissions, end to end ---------------------------------------------------


def _audit_column(store, prompts, mid):
    """A cross-cutting slice with a full column, classified as one."""
    for layer, title, parent in (
        ("B", "Every state change is recorded", "I·01"),
        ("A", "An append-only event log", "B·02"),
        ("S", "audit/log.py appends a record", "A·02"),
    ):
        call(
            store,
            prompts,
            "POST",
            "/projects/m/amendments",
            {
                "slice": "audit",
                "in_response_to": mid,
                "entries": [
                    {"layer": layer, "title": title, "derives_from": [parent]}
                ],
            },
        )
    call(store, prompts, "POST", "/projects/m/slices/audit/type",
         {"type": "cross_cutting"})


def test_a_slice_may_emit_into_a_cross_cutting_slice(store, prompts):
    mid = seed(store, prompts)
    _audit_column(store, prompts, mid)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "search/index.py records every query",
                    "derives_from": ["A·01"],
                    "emits_into": ["S·02"],
                }
            ],
        },
    )
    assert (status, payload["admitted"]) == (200, True)
    _, spine = call(store, prompts, "GET", "/projects/m/spine?layer=S")
    row = [r for r in spine["spine"] if r["id"] == "S·03"][0]
    assert row["emits_into"] == ["S·02"]
    assert row["depends_on"] == []


def test_an_emission_imposes_no_order_on_the_concern(store, prompts):
    """listings emits into audit, and audit does not become a dependency of
    listings. That is what lets the concern be derived first."""
    mid = seed(store, prompts)
    _audit_column(store, prompts, mid)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "search/index.py records every query",
                    "derives_from": ["A·01"],
                    "emits_into": ["S·02"],
                }
            ],
        },
    )
    manifest = store.load_manifest("m")
    assert manifest.dependency_graph(store.load_graph("m"))["listings"] == ()
    _, wp = call(store, prompts, "GET", "/projects/m/work-package?slice=listings")
    assert wp["read_set"] == []
    assert [e["id"] for e in wp["cross_cutting"]] == ["S·02"]


def test_depending_on_a_cross_cutting_slice_is_refused_end_to_end(store, prompts):
    mid = seed(store, prompts)
    _audit_column(store, prompts, mid)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "search/index.py asks the log",
                    "derives_from": ["A·01"],
                    "depends_on": ["S·02"],
                }
            ],
        },
    )
    assert (status, payload["admitted"]) == (409, False)
    assert payload["findings"][0]["kind"] == "bad_emission"
    assert "emits_into, not depends_on" in payload["findings"][0]["detail"]


def test_emitting_into_an_ordinary_slice_is_refused_end_to_end(store, prompts):
    mid = _two_columns(store, prompts)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "search/index.py publishes to payouts",
                    "derives_from": ["A·01"],
                    "emits_into": ["S·02"],
                }
            ],
        },
    )
    assert (status, payload["admitted"]) == (409, False)
    assert "not cross-cutting" in payload["findings"][0]["detail"]


# -- waves -------------------------------------------------------------------


def test_waves_are_computed_from_the_dependency_graph(store, prompts):
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    _, payload = call(store, prompts, "GET", "/projects/m/waves")
    assert payload["waves"] == [
        {"wave": 0, "slices": ["listings"], "width": 1},
        {"wave": 1, "slices": ["payouts"], "width": 1},
    ]
    assert payload["wave_of"] == {"listings": 0, "payouts": 1}
    assert payload["unschedulable"] == []
    assert payload["chain"] is True


def test_independent_slices_share_a_wave(store, prompts):
    _two_columns(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/waves")
    assert payload["waves"] == [
        {"wave": 0, "slices": ["listings", "payouts"], "width": 2}
    ]
    assert payload["chain"] is False


def test_a_cross_cutting_slice_is_in_wave_zero(store, prompts):
    mid = seed(store, prompts)
    _audit_column(store, prompts, mid)
    _, payload = call(store, prompts, "GET", "/projects/m/waves")
    assert payload["wave_of"]["audit"] == 0


# -- issues and reconciliation, end to end -----------------------------------


def _issue(store, prompts, target, kind, claim, raised_by=None, round=1):
    return call(
        store,
        prompts,
        "POST",
        "/projects/m/issues",
        {
            "target": target,
            "kind": kind,
            "claim": claim,
            "raised_by": raised_by,
            "round": round,
            "assumption": "assumed the old shape holds",
        },
    )


def test_an_issue_resolves_its_target_slice_when_filed(store, prompts):
    """Resolved at filing time, so grouping later cannot be thrown off by
    membership that has since moved."""
    _two_columns(store, prompts)
    status, payload = _issue(store, prompts, "S·01", "additive", "needs a size")
    assert status == 201
    assert payload["target_slice"] == "listings"
    assert payload["status"] == "open"


def test_an_issue_against_a_malformed_target_is_a_caller_error(store, prompts):
    _two_columns(store, prompts)
    status, payload = _issue(store, prompts, "nonsense", "additive", "x")
    assert status == 400
    assert "malformed identifier" in payload["error"]


def test_reconciliation_groups_open_issues_by_target_slice(store, prompts):
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    _issue(store, prompts, "S·01", "additive", "needs a size", "payouts")
    _issue(store, prompts, "S·01", "additive", "needs a count", "payouts")
    _issue(store, prompts, "S·02", "additive", "needs a total", "listings")
    _, payload = call(store, prompts, "GET", "/projects/m/reconcile")
    assert [(b["slice"], b["wave"], len(b["issues"])) for b in payload["batches"]] == [
        ("listings", 0, 2),
        ("payouts", 1, 1),
    ]
    assert payload["escalations"] == []


def test_a_batch_carries_the_rerun_scope_of_its_semantic_issues(store, prompts):
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    _issue(store, prompts, "S·01", "semantic", "it returns ids not names",
           "listings")
    _, payload = call(store, prompts, "GET", "/projects/m/reconcile")
    assert payload["batches"][0]["rerun"] == ["S·03"]


def test_an_additive_issue_leaves_the_rerun_scope_empty(store, prompts):
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    _issue(store, prompts, "S·01", "additive", "needs a size", "listings")
    _, payload = call(store, prompts, "GET", "/projects/m/reconcile")
    assert payload["batches"][0]["rerun"] == []


def test_the_router_sees_headers_not_assumptions(store, prompts):
    """Roughly thirty tokens an issue. The assumption is stored and readable,
    but never in what the router loads."""
    _two_columns(store, prompts)
    _issue(store, prompts, "S·01", "additive", "needs a size")
    _, payload = call(store, prompts, "GET", "/projects/m/reconcile")
    header = payload["batches"][0]["issues"][0]
    assert "assumption" not in header
    assert set(header) == {
        "id", "target", "target_slice", "raised_by", "kind", "claim", "round"
    }
    _, listed = call(store, prompts, "GET", "/projects/m/issues")
    assert listed["issues"][0]["assumption"] == "assumed the old shape holds"


def test_a_semantic_issue_reaching_back_a_wave_is_escalated(store, prompts):
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    _issue(store, prompts, "S·01", "semantic", "it means something else",
           "payouts")
    _, payload = call(store, prompts, "GET", "/projects/m/reconcile")
    assert payload["batches"] == []
    assert payload["escalations"][0]["reason"] == "reaches_back"


def test_an_issue_past_the_round_cap_is_escalated(store, prompts):
    _two_columns(store, prompts)
    _issue(store, prompts, "S·01", "additive", "again", "payouts", round=3)
    _, payload = call(store, prompts, "GET", "/projects/m/reconcile")
    assert payload["escalations"][0]["reason"] == "round_cap"


def test_a_closed_issue_leaves_the_queue(store, prompts):
    _two_columns(store, prompts)
    _issue(store, prompts, "S·01", "additive", "needs a size")
    call(store, prompts, "POST", "/projects/m/issues/iss-0001/close",
         {"status": "resolved", "note": "added", "produced": ["S·09"]})
    _, payload = call(store, prompts, "GET", "/projects/m/reconcile")
    assert payload["batches"] == []
    _, listed = call(store, prompts, "GET", "/projects/m/issues?status=resolved")
    assert listed["issues"][0]["resolution"]["produced"] == ["S·09"]


def test_issues_are_kept_apart_from_the_inbox(store, prompts):
    """The inbox is what the outside world wants; the issue queue is what one
    part of the pipeline needs from another. Conflating them would put a
    request nobody outside ever made into the queue a human reads."""
    _two_columns(store, prompts)
    _issue(store, prompts, "S·01", "additive", "needs a size")
    _, messages = call(store, prompts, "GET", "/inbox")
    assert not any("needs a size" in m["title"] for m in messages["messages"])


# -- spec to code, end to end ------------------------------------------------


def _implemented(store, prompts):
    """The seeded project with S·01 implemented by a module."""
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    call(store, prompts, "POST", "/projects/m/modules",
         {"path": "search/index.py", "implements": ["S·01"]})
    call(store, prompts, "POST", "/projects/m/modules",
         {"path": "payouts/ledger.py", "implements": ["S·03"]})
    return mid


def test_a_module_declares_what_it_implements(store, prompts):
    _two_columns(store, prompts)
    status, payload = call(store, prompts, "POST", "/projects/m/modules",
                           {"path": "search/index.py", "implements": ["S·01"]})
    assert (status, payload["implements"]) == (200, ["S·01"])


def test_spec_entries_nothing_implements_are_listed(store, prompts):
    """The bottom layer's version of unserved: stated, nothing built."""
    _two_columns(store, prompts)
    call(store, prompts, "POST", "/projects/m/modules",
         {"path": "search/index.py", "implements": ["S·01"]})
    _, payload = call(store, prompts, "GET", "/projects/m/modules")
    assert payload["unimplemented"] == ["S·02"]


def test_a_module_claiming_a_layer_above_spec_is_refused(store, prompts):
    _two_columns(store, prompts)
    status, payload = call(store, prompts, "POST", "/projects/m/modules",
                           {"path": "search/index.py", "implements": ["A·01"]})
    assert status == 400
    assert "implements spec entries" in payload["error"]


def test_the_first_plan_is_the_whole_spec_layer(store, prompts):
    _implemented(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/plan")
    assert payload["issued"] is True
    assert payload["diff"]["added"] == ["S·01", "S·02", "S·03"]
    assert payload["mark"] == 3


def test_a_supersession_produces_a_write_set_and_a_read_set(store, prompts):
    """search/index.py implemented S·01 and must change. payouts/ledger.py
    consumed S·01's meaning through S·03 and is read-only context."""
    mid = _implemented(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "a different index module",
                    "derives_from": ["A·01"],
                    "supersedes": "S·01",
                }
            ],
        },
    )
    # S·03 still points at retired S·01, so re-derive it before planning.
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "payouts",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "ledger reads the new index",
                    "derives_from": ["A·02"],
                    "depends_on": ["S·04"],
                    "supersedes": "S·03",
                }
            ],
        },
    )
    call(store, prompts, "POST", "/projects/m/modules",
         {"path": "payouts/ledger.py", "implements": ["S·05"]})
    _, payload = call(store, prompts, "GET", "/projects/m/plan?since=3")
    assert payload["diff"]["retired"] == ["S·01", "S·03"]
    assert [r["path"] for r in payload["write_set"]] == [
        "payouts/ledger.py",
        "search/index.py",
    ]


def test_planning_is_refused_while_the_graph_is_unsound(store, prompts):
    """Planning from a broken chain produces code derived from a lie."""
    mid = _implemented(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {
                    "layer": "S",
                    "title": "a different index module",
                    "derives_from": ["A·01"],
                    "supersedes": "S·01",
                }
            ],
        },
    )
    status, payload = call(store, prompts, "GET", "/projects/m/plan?since=3")
    assert (status, payload["issued"]) == (409, False)


def test_the_audit_compares_touched_files_against_the_write_set(store, prompts):
    _implemented(store, prompts)
    _, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/audit",
        {
            "touched": ["search/index.py", "search/secret_helper.py"],
            "editable_paths": ["search/index.py"],
        },
    )
    assert payload["clean"] is False
    assert payload["undeclared"] == ["search/secret_helper.py"]


def test_the_audit_can_derive_the_write_set_itself(store, prompts):
    _implemented(store, prompts)
    _, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/audit",
        {"touched": ["search/index.py", "payouts/ledger.py"], "since": 0},
    )
    assert payload["clean"] is True


# -- lifecycle and analysis, end to end --------------------------------------


def test_the_raw_request_is_recorded_as_it_was_worded(store, prompts):
    """The only unprocessed human signal in the system. Everything below it
    is something an agent derived."""
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/events?kind=request")
    first = payload["events"][0]
    assert first["facts"]["title"] == "a marketplace"
    assert first["facts"]["type"] == "initiate"


def test_a_correction_records_the_layer_it_entered_at(store, prompts):
    """DESIGN.md §9's diagnostic. Once the fix has propagated the graph just
    looks correct and the evidence is gone."""
    seed(store, prompts)
    _, msg = post(store, prompts, "correction", "search ranking is wrong")
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": msg["message_id"],
            "entries": [
                {
                    "layer": "B",
                    "title": "ranked by recency, not relevance",
                    "derives_from": ["I·01"],
                    "supersedes": "B·01",
                }
            ],
        },
    )
    _, payload = call(store, prompts, "GET", "/projects/m/events?kind=correction")
    assert payload["events"][0]["facts"]["entered_at"] == "behaviour"
    assert payload["events"][0]["refs"] == ["B·01"]


def test_a_refusal_is_recorded_even_though_it_changed_nothing(store, prompts):
    """A refusal leaves no trace in the graph it was refused from. If it is
    not recorded here it never happened."""
    mid = seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {"layer": "A", "title": "orphan", "derives_from": ["B·99"]}
            ],
        },
    )
    _, payload = call(store, prompts, "GET", "/projects/m/events?kind=refusal")
    assert payload["events"][0]["facts"]["reason"] == "broken edge"


def test_an_assumption_is_kept_where_analysis_can_find_it(store, prompts):
    """§6 calls declaring what you could not derive the thing that makes
    gates real. Across projects these map where the pipeline is too thin."""
    _two_columns(store, prompts)
    _issue(store, prompts, "S·01", "additive", "no way to ask for its size")
    _, payload = call(store, prompts, "GET", "/projects/m/events?kind=issue_raised")
    assert payload["events"][0]["facts"]["assumption"] == "assumed the old shape holds"


def test_events_page_by_sequence(store, prompts):
    seed(store, prompts)
    _, first = call(store, prompts, "GET", "/projects/m/events")
    mark = first["next_since"]
    _, again = call(store, prompts, "GET", f"/projects/m/events?since={mark}")
    assert again["events"] == []


def test_insights_report_change_locality(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/insights")
    assert payload["change_locality"]["mean"] == 1.0
    assert payload["change_locality"]["single_slice"] == 1


def test_insights_are_inputs_never_a_verdict(store, prompts):
    seed(store, prompts)
    _, payload = call(store, prompts, "GET", "/projects/m/insights")
    assert "verdict" not in payload
    assert "not a judgement" in payload["note"]


def test_candidates_expose_shared_parentage(store, prompts):
    mid = seed(store, prompts)
    call(
        store,
        prompts,
        "POST",
        "/projects/m/amendments",
        {
            "slice": "listings",
            "in_response_to": mid,
            "entries": [
                {"layer": "B", "title": "filter", "derives_from": ["I·01"]}
            ],
        },
    )
    _, payload = call(store, prompts, "GET", "/projects/m/slicing/candidates")
    assert payload["shared_parentage"][0]["pair"] == ["B·01", "B·02"]


def test_a_proposal_can_be_scored_without_creating_it(store, prompts):
    _two_columns(store, prompts)
    before = set(store.load_manifest("m").slices)
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/slicing/score",
        {"proposal": {"everything": ["S·01", "S·02"]}},
    )
    assert (status, payload["legal"]) == (200, True)
    assert set(store.load_manifest("m").slices) == before


def test_a_proposal_that_would_be_illegal_says_so_without_refusing(store, prompts):
    """Scoring never gates. It reports that the cut is illegal and returns
    200 -- refusing would make trialling impossible."""
    mid = _two_columns(store, prompts)
    _spec_in(store, prompts, mid, "payouts", "ledger reads the index", "A·02",
             depends_on=["S·01"])
    status, payload = call(
        store,
        prompts,
        "POST",
        "/projects/m/slicing/score",
        {"proposal": {"a": ["S·01"], "b": ["S·02"], "c": ["S·03"]}},
    )
    assert status == 200
    assert any("probably not a slice" in w for w in payload["warnings"])
