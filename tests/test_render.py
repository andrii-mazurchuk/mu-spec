"""Turning a work unit into a ticket: title, labels, body.

A pure function over the payload `get_work_unit` already returns, so the
assembly lives in one place and this module needs no store, no graph and no
socket to test.

What it deliberately does NOT do is resolve `blocked_by` to issue numbers.
Those do not exist until the issues do, so the ticket carries UNIT KEYS and
whoever creates the issues maps them. Keeping that out means rendering is
decided entirely by the graph, and can be read and reviewed before anything
is created anywhere.
"""

from __future__ import annotations

import pytest

from mu_spec.render import MAX_TITLE, render

IMPL = {
    "project": "dark",
    "issued": True,
    "unit": {
        "key": "S·01",
        "anchor": "S·01",
        "entries": ["S·01"],
        "modules": ["dark/config/declare.py"],
        "slices": ["legibility"],
        "size": {"entries": 1, "modules": 1, "body_bytes": 2649},
    },
    "write_set": ["dark/config/declare.py"],
    "entries": [
        {
            "id": "S·01",
            "layer": "spec",
            "title": "`declare.py` is the declaration primitive",
            "body": "Module: `dark/config/declare.py`. Stdlib only.",
        }
    ],
    "justification": [
        {"id": "I·12", "title": "Every artifact must be intelligible"},
        {"id": "A·02", "title": "A setting's meaning is declared where it is",
         "body": "the full parent body"},
    ],
    "read_set": [],
    "cross_cutting": [],
    "file_scope": {
        "dark/config/declare.py": [
            {"id": "S·02", "title": "`Provisional` is a declaration-site attribute"}
        ]
    },
    "follows": ["S·01:T"],
    "followed_by": [],
    "overlap": ["S·02"],
    "tests": [
        {"id": "T·30", "title": "A setting without its description is not constructible"}
    ],
    "tests_pending": [],
    "audit": {
        "editable_paths": ["dark/config/declare.py"],
        "rule": "any file touched outside editable_paths is a write this unit "
        "never declared",
    },
}


def _test_unit() -> dict:
    """The same unit's scenarios: a test unit, which follows nothing."""
    return {
        **IMPL,
        "unit": {**IMPL["unit"], "key": "S·01:T", "entries": ["T·30", "T·31"],
                 "modules": ["tests/test_config.py"]},
        "write_set": ["tests/test_config.py"],
        "entries": [
            {"id": "T·30", "layer": "test", "title": "not constructible",
             "purpose": "the omitted-description case", "body": "Declare a setting."},
            {"id": "T·31", "layer": "test", "title": "blank is rejected",
             "purpose": "the whitespace case", "body": "Declare with spaces."},
        ],
        "follows": [],
        "tests": [],
        "file_scope": {"tests/test_config.py": []},
    }


# -- the title --------------------------------------------------------------


def test_the_title_is_the_key_and_what_the_unit_writes():
    """Computable, which it was not under the old grain. A unit held several
    entries then, so `a12837b4` ruled naming was judgement. A unit is now one
    entry and its modules, and the module paths name the work better than the
    entry's title does -- `S·01`'s title is a 150-character sentence."""
    assert render(IMPL).title == "S·01 — dark/config/declare.py"


def test_the_title_stays_short_when_a_unit_writes_many_files():
    """`dark/S·73` writes five files. A title is a line in a list, not a
    manifest, and GitHub allows 256 characters -- neither is a reason to spend
    them."""
    many = {
        **IMPL,
        "write_set": [f"a/very/long/path/number_{n}.py" for n in range(9)],
    }
    title = render(many).title
    assert len(title) <= MAX_TITLE
    assert title.startswith("S·01 — a/very/long/path/number_0.py")
    assert "more" in title


def test_a_unit_writing_nothing_still_gets_a_usable_title():
    """Degradation: a module map mid-edit can leave a unit with no paths, and
    a ticket with an empty title is worse than an unhelpful one."""
    assert render({**IMPL, "write_set": []}).title == "S·01"


# -- labels -----------------------------------------------------------------


def test_labels_carry_the_anchor_the_slice_and_the_kind():
    assert render(IMPL).labels == (
        "mu-spec:S·01", "kind:implementation", "slice:legibility",
    )


def test_a_test_unit_is_labelled_as_one():
    assert "kind:test" in render(_test_unit()).labels


def test_a_unit_straddling_slices_gets_one_label_each():
    """`slices` is a list because a file can straddle. One label per slice
    rather than a comma-joined one, so filtering by either works."""
    straddle = {**IMPL, "unit": {**IMPL["unit"], "slices": ["legibility", "record"]}}
    labels = render(straddle).labels
    assert "slice:legibility" in labels and "slice:record" in labels


# -- ordering ---------------------------------------------------------------


def test_blocked_by_is_unit_keys_and_never_issue_numbers():
    """Issue numbers do not exist until the issues do. Rendering is decided by
    the graph alone, so it can be reviewed before anything is created."""
    assert render(IMPL).blocked_by == ("S·01:T",)
    assert render(_test_unit()).blocked_by == ()


# -- the body ---------------------------------------------------------------


def test_the_body_carries_the_contract_verbatim():
    body = render(IMPL).body
    assert "## Contract" in body
    assert "Module: `dark/config/declare.py`. Stdlib only." in body


def test_a_scenario_carries_its_purpose():
    """`purpose` exists on test entries only, and says why the scenario is
    worth having rather than what it checks."""
    body = render(_test_unit()).body
    assert "the omitted-description case" in body


def test_the_write_set_and_the_audit_rule_travel_together():
    body = render(IMPL).body
    assert "dark/config/declare.py" in body
    assert "never declared" in body, "the audit rule is the point of the list"


def test_overlap_is_stated_as_not_at_the_same_time_never_as_an_order():
    """The one thing a reader must not conclude from `overlap` is a direction.
    Neither unit waits for the other."""
    body = render(IMPL).body
    assert "S·02" in body
    assert "same time" in body.lower()


def test_file_scope_names_the_other_contracts_a_file_must_serve():
    body = render(IMPL).body
    assert "`Provisional` is a declaration-site attribute" in body


def test_empty_sections_are_omitted_rather_than_rendered_blank():
    """An empty heading reads as "nothing to say here", which is a claim. On
    `S·01` three of the nine sections are empty."""
    body = render(IMPL).body
    assert "Read-only context" not in body
    assert "not yet implemented" not in body


def test_a_section_appears_as_soon_as_it_has_content():
    with_reads = {
        **IMPL,
        "read_set": [{"id": "S·09", "title": "the ledger reads the index"}],
        "tests_pending": ["T·44"],
    }
    body = render(with_reads).body
    assert "S·09" in body and "T·44" in body


def test_the_sections_come_in_a_fixed_order():
    """A builder reads top-down and stops when it has enough. What to build
    comes before why it exists, and what may be written comes before both --
    an agent that reads only the first screen must still know its scope."""
    body = render(IMPL).body
    order = [body.index(h) for h in (
        "## Contract", "## Files you may write", "## Blocked by",
        "## Must not run", "## Other contracts", "## Why this exists",
    )]
    assert order == sorted(order)


# -- refusing to render nonsense --------------------------------------------


def test_an_unissued_payload_is_refused_rather_than_rendered():
    """`get_work_unit` answers `{issued: false, reason: ...}` on an unsound
    graph. Rendering that produces a ticket whose contract section is an error
    message, which is the kind of thing nobody notices until an agent tries to
    build it."""
    with pytest.raises(ValueError, match="not issued"):
        render({"project": "dark", "issued": False, "reason": "graph is unsound"})


def test_a_payload_missing_its_unit_is_refused():
    with pytest.raises(ValueError):
        render({"project": "dark", "issued": True})


# -- the size ceiling -------------------------------------------------------


def test_an_oversized_body_is_reported_and_never_truncated():
    """Measured on `dark`: largest real body 44,741 characters against
    GitHub's 65,536. Comfortable, and not so comfortable that nobody should
    check -- a project with a scenario-heavy test unit half again as large
    would cross it.

    Truncating is the wrong answer: a contract cut off mid-sentence still
    looks like a contract, and an agent builds from what it was given. So the
    ticket says it is oversized and whoever creates issues skips it with a
    reason, instead of taking a 422 that names nothing.
    """
    from mu_spec.render import BODY_LIMIT

    assert render(IMPL).oversized is False
    huge = {
        **IMPL,
        "entries": [{**IMPL["entries"][0], "body": "x" * (BODY_LIMIT + 1)}],
    }
    ticket = render(huge)
    assert ticket.oversized is True
    assert len(ticket.body) > BODY_LIMIT, "the body is intact, not shortened"
    # The run itself, not a count of the letter -- "exists" and "scope" carry
    # their own x's from the section prose.
    assert "x" * (BODY_LIMIT + 1) in ticket.body


def test_list_items_are_single_spaced():
    """Parts are joined with a blank line, which is right between blocks and
    wrong between list items -- markdown renders those as a loose list, and it
    costs real bytes across 144 tickets with a 16 KB median."""
    body = render({**IMPL, "write_set": ["a.py", "b.py", "c.py"]}).body
    assert "- `a.py`\n- `b.py`\n- `c.py`" in body
