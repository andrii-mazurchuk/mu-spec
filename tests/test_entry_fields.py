"""An amendment refuses what it would otherwise drop.

A per-entry `slice` used to be ignored, and a replacement for a sliced entry
landed unassigned when the amendment named no slice: fernhollow-boxes
re-issued B-102..B-105 as B-106..B-109 for it.
"""

from __future__ import annotations

import pytest

from test_server import call, prompts, seed, store  # noqa: F401  (fixtures)


def amend(store, prompts, mid, entry, **top):
    return call(store, prompts, "POST", "/projects/m/amendments",
                {"in_response_to": mid, "entries": [entry], **top})


B = {"layer": "B", "title": "a buyer can filter listings", "derives_from": ["I-01"]}


@pytest.mark.parametrize("field", ["slice", "sliec", "kind"])
def test_an_unknown_entry_field_is_refused_not_dropped(store, prompts, field):
    mid = seed(store, prompts)
    status, out = amend(store, prompts, mid, {**B, field: "listings"}, slice="listings")
    assert status == 400
    assert field in out["error"]
    assert "B-02" not in str(store.load_manifest("m").slices["listings"].members)


def test_a_per_entry_slice_points_at_the_amendment_level_one(store, prompts):
    mid = seed(store, prompts)
    _, out = amend(store, prompts, mid, {**B, "slice": "listings"})
    assert "amendment" in out["error"]


def test_replacing_a_sliced_entry_must_name_a_slice(store, prompts):
    mid = seed(store, prompts)
    status, out = amend(store, prompts, mid, {**B, "supersedes": "B-01"})
    assert status == 400
    assert "listings" in out["error"]


def test_replacing_a_sliced_entry_into_a_named_slice_lands(store, prompts):
    mid = seed(store, prompts)
    status, _ = amend(store, prompts, mid, {**B, "supersedes": "B-01"}, slice="listings")
    assert status == 200
    members = {str(i) for i in store.load_manifest("m").slices["listings"].members}
    assert "B-02" in members


def test_a_new_entry_still_needs_no_slice_where_none_was_needed(store, prompts):
    mid = seed(store, prompts)
    status, _ = amend(store, prompts, mid, B)
    assert status == 200
