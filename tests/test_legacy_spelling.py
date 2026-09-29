"""Data written before the separator became ASCII must still read.

The separator changed from U+00B7 MIDDLE DOT to a hyphen, and the unit key's
test suffix from `:T` to `-T`. Three live projects and every history file in
them hold the old spelling, and those files are append-only -- rewriting an
audit trail to change punctuation is exactly the edit-in-place this unit
refuses everywhere else.

So the rule is: **parse both, write one.** These tests are that rule, and each
one names the failure it prevents, because every one of them was a real defect
found by running the change against a copy of the live `dark` project rather
than by reading the diff.
"""

from __future__ import annotations

import json

from mu_spec.emit import read_log
from mu_spec.identifiers import parse
from mu_spec.units import anchor_of, canonical_key, is_test_key


OLD = "S·01"
OLD_TEST = "S·01:T"


# -- identifiers -------------------------------------------------------------


def test_both_spellings_parse_to_the_same_identifier():
    assert parse(OLD) == parse("S-01")
    assert parse("T·344") == parse("T-344")


def test_only_the_ascii_spelling_is_ever_written():
    assert str(parse(OLD)) == "S-01"
    assert str(parse("A·07")) == "A-07"


# -- unit keys ---------------------------------------------------------------


def test_a_legacy_key_is_recognised_as_a_test_unit():
    """`render` asks this to set `kind`, which is what a consumer routes on.
    The suffix was hardcoded at two call sites, so changing the constant alone
    would have quietly relabelled every test unit an implementation unit."""
    assert is_test_key(OLD_TEST)
    assert is_test_key("S-01-T")
    assert not is_test_key(OLD)
    assert not is_test_key("S-01")


def test_a_legacy_key_re_spells_to_the_current_form():
    assert canonical_key(OLD_TEST) == "S-01-T"
    assert canonical_key(OLD) == "S-01"
    assert canonical_key("S-01-T") == "S-01-T", "already current, unchanged"


def test_an_unrecognisable_key_is_left_alone():
    """This normalises the keys it recognises and makes no claim about what a
    key may be."""
    assert canonical_key("not-a-key") == "not-a-key"
    assert canonical_key("") == ""


def test_the_anchor_comes_off_either_spelling():
    assert anchor_of(OLD_TEST) == OLD
    assert anchor_of("S-01-T") == "S-01"


# -- the emission log, where a mismatch duplicates real issues ---------------


def _log(tmp_path, *lines):
    path = tmp_path / "emissions.jsonl"
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return path


def test_a_legacy_emission_is_recognised_rather_than_emitted_again(tmp_path):
    """The one that would have cost real money and a second withdrawal.

    The log is keyed by unit key. An emission recorded in the old spelling
    would not match a freshly computed key, so the guard would conclude
    nothing had been emitted and create every issue a second time -- the exact
    duplication this log exists to prevent.
    """
    path = _log(tmp_path, {
        "seq": 1, "at": 1.0, "cut_seq": 3, "repo": "o/n",
        "issues": {OLD_TEST: {"id": 1, "number": 1, "url": "u"},
                   OLD: {"id": 2, "number": 2, "url": "u"}},
        "wired": [f"{OLD}<-{OLD_TEST}"],
    })
    emissions, _rollbacks = read_log(path)
    assert set(emissions[0].issues) == {"S-01-T", "S-01"}
    assert emissions[0].wired == ("S-01<-S-01-T",)


def test_a_legacy_rollback_still_subtracts(tmp_path):
    """Otherwise a withdrawn unit looks un-withdrawn and the re-emission that
    the withdrawal was FOR creates nothing."""
    path = _log(
        tmp_path,
        {"seq": 1, "at": 1.0, "cut_seq": 3, "repo": "o/n",
         "issues": {OLD: {"id": 2, "number": 2, "url": "u"}}, "wired": []},
        {"kind": "rollback", "seq": 2, "at": 2.0, "cut_seq": 3, "repo": "o/n",
         "undone": [1], "closed": {OLD: 2}, "failed": []},
    )
    _emissions, rollbacks = read_log(path)
    assert rollbacks[0].closed == {"S-01": 2}


def test_reading_a_legacy_log_rewrites_nothing(tmp_path):
    """Append-only means append-only. The re-spelling happens in memory; the
    audit trail keeps the bytes it was written with."""
    path = _log(tmp_path, {
        "seq": 1, "at": 1.0, "cut_seq": 3, "repo": "o/n",
        "issues": {OLD: {"id": 2, "number": 2, "url": "u"}}, "wired": [],
    })
    before = path.read_bytes()
    read_log(path)
    assert path.read_bytes() == before
