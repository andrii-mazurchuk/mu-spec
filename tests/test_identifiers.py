from __future__ import annotations

import pytest

from mu_spec.identifiers import (
    LAYERS,
    Identifier,
    InvalidIdentifier,
    is_upward,
    parse,
    sort_key,
)


# -- parsing and formatting -------------------------------------------------


def test_parses_the_canonical_form():
    ident = parse("B·14")
    assert ident == Identifier(layer="B", number=14)


def test_round_trips_through_str():
    assert str(parse("S·31")) == "S·31"


def test_numbers_below_ten_are_zero_padded():
    """The design doc writes A·07, not A·7. Padding is cosmetic but it is the
    written form, and spines are read by humans."""
    assert str(Identifier(layer="A", number=7)) == "A·07"


def test_numbers_above_ninety_nine_are_not_truncated():
    assert str(Identifier(layer="B", number=1234)) == "B·1234"


@pytest.mark.parametrize(
    "text",
    [
        "B14",  # no separator
        "B.14",  # wrong separator -- the canonical one is U+00B7
        "B-14",
        "X·14",  # unknown layer
        "B·",  # no number
        "·14",  # no layer
        "B·1a",
        "B·-3",
        "b·14",  # layers are upper case
        "",
        "B·14·2",
    ],
)
def test_rejects_malformed_identifiers(text):
    with pytest.raises(InvalidIdentifier):
        parse(text)


def test_zero_is_not_a_valid_number():
    """Numbering starts at 1. A zero would make 'no entries yet' and 'the
    first entry' indistinguishable in the allocator."""
    with pytest.raises(InvalidIdentifier):
        parse("B·00")


# -- layer ordering ---------------------------------------------------------


def test_layers_run_from_intent_down_to_spec():
    """Order is load-bearing: it is what 'upward' means. Code is deliberately
    absent -- it is represented by module backlinks to spec identifiers, not
    by entries in this graph."""
    assert LAYERS == ("I", "B", "A", "S")


def test_is_upward_is_true_toward_intent():
    assert is_upward(parse("S·01"), parse("A·01")) is True
    assert is_upward(parse("A·01"), parse("B·01")) is True
    assert is_upward(parse("B·01"), parse("I·01")) is True


def test_is_upward_allows_skipping_a_layer():
    """A behaviour may derive straight from intent, and a spec entry may
    serve an architecture entry two layers up. Requiring strictly adjacent
    layers would force filler entries that say nothing."""
    assert is_upward(parse("S·01"), parse("I·01")) is True


def test_is_upward_is_false_downward_and_sideways():
    assert is_upward(parse("I·01"), parse("B·01")) is False
    assert is_upward(parse("B·01"), parse("B·02")) is False
    assert is_upward(parse("B·01"), parse("B·01")) is False


# -- ordering ---------------------------------------------------------------


def test_sorts_by_layer_then_numerically_not_lexically():
    """B·9 must sort before B·10. Lexical ordering on the rendered string
    would put B·10 first and quietly scramble every spine."""
    ids = [parse("B·10"), parse("I·02"), parse("B·09"), parse("S·01")]
    assert [str(i) for i in sorted(ids, key=sort_key)] == [
        "I·02",
        "B·09",
        "B·10",
        "S·01",
    ]


def test_identifiers_are_hashable_and_compare_by_value():
    assert parse("B·14") == parse("B·14")
    assert len({parse("B·14"), parse("B·14")}) == 1
    assert parse("B·14") != parse("B·15")
