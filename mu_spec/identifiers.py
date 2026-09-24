"""Entry identifiers: parsing, formatting, and the layer ordering that
gives "upward" its meaning.

An identifier is a layer prefix, a separator, and a flat number -- `B·14`,
`A·07`, `S·31`. Two rules from the design doc govern everything here, and
both are the reason this module exists rather than the format being an
f-string at the call site:

- Identifiers are never reused and never renumbered. Renumbering silently
  rots every historical reference.
- Identifiers encode **layer and creation order only, never slice**. Slice
  membership lives in the manifest, as a set. Nothing in this module knows
  what a slice is, and nothing here may ever grow a slice field -- that is
  precisely what lets a slice split without renumbering anything.
"""

from __future__ import annotations

import dataclasses
import re

# Order is load-bearing: index in this tuple is a layer's depth, and "upward"
# means toward intent. Code is deliberately not a layer here -- it is
# represented by module backlinks to spec identifiers, not by entries in this
# graph, so it has no identifiers of its own to allocate.
LAYERS = ("I", "B", "A", "S")

# Tests are a valid layer that is deliberately NOT in the derivation chain.
#
# A test forks off spec rather than continuing downward from it: an
# implementation module implements spec entries, a test module implements
# test entries, and nothing ever derives from a test. Putting T inside
# LAYERS would make spec stop being the bottom of the chain, and the
# completeness gate -- which asks whether knowledge has reached the bottom
# -- would start reporting every untested spec entry as unserved. That is a
# different question with a different consequence, and it has its own report.
#
# What T *does* need is a position, so a spine sorts it last and so "one
# layer up from a test" resolves to spec by the same arithmetic everything
# else uses. Hence a depth past the end of the chain rather than a place in
# it.
TEST = "T"
SPEC = "S"
ALL_LAYERS = LAYERS + (TEST,)

LAYER_NAMES = {
    "I": "intent",
    "B": "behaviour",
    "A": "architecture",
    "S": "spec",
    "T": "test",
}

# U+00B7 MIDDLE DOT, the form the design doc is written in.
SEPARATOR = "·"

_PATTERN = re.compile(rf"^([A-Z])\{SEPARATOR}([0-9]+)$")

# Rendered width of the number. Purely cosmetic -- `A·07` is how the design
# doc writes it, and spines are read by humans -- and never used for parsing
# or comparison, both of which are numeric.
_PAD = 2


class InvalidIdentifier(ValueError):
    """Raised when a string is not a well-formed identifier. A hard error, not
    a degraded empty value: a malformed identifier means the graph would
    silently lose an edge, which is the one failure this unit exists to make
    impossible."""


@dataclasses.dataclass(frozen=True)
class Identifier:
    layer: str
    number: int

    def __post_init__(self) -> None:
        if self.layer not in ALL_LAYERS:
            raise InvalidIdentifier(
                f"unknown layer {self.layer!r}, expected one of {ALL_LAYERS}"
            )
        if self.number < 1:
            raise InvalidIdentifier(
                f"identifier numbers start at 1, got {self.number}"
            )

    def __str__(self) -> str:
        return f"{self.layer}{SEPARATOR}{self.number:0{_PAD}d}"

    @property
    def depth(self) -> int:
        """Position in the derivation chain. Lower is closer to intent.

        A test sits one past the end. That is what makes `derives_legally`
        resolve a test's parent to spec without a special case, and what
        sorts tests last in a spine -- while keeping spec the bottom of the
        chain for anything asking how far knowledge has been carried down.
        """
        if self.layer == TEST:
            return len(LAYERS)
        return LAYERS.index(self.layer)

    @property
    def layer_name(self) -> str:
        return LAYER_NAMES[self.layer]


def parse(text: str) -> Identifier:
    match = _PATTERN.match(text.strip() if isinstance(text, str) else "")
    if match is None:
        raise InvalidIdentifier(f"malformed identifier: {text!r}")
    return Identifier(layer=match.group(1), number=int(match.group(2)))


def derives_legally(source: Identifier, target: Identifier) -> bool:
    """True when `source` may declare `target` as a parent: `target` must sit
    exactly one layer closer to intent.

    Adjacent only -- skipping is refused. A spec entry deriving straight from
    intent claims a derivation that was never written down, so the layer it
    jumped over cannot be reviewed and cannot be re-derived when the intent
    changes. It also keeps the two edge kinds unambiguous: `derives_from` is
    exactly one layer up, `depends_on` is exactly the same layer, and nothing
    else is expressible.

    Tests need no case of their own. A test sits one past spec, so "exactly
    one layer up" already means spec and nothing else -- and since no layer
    sits past a test, nothing can legally derive from one. Both halves of
    the test contract fall out of the arithmetic rather than being asserted
    on top of it.
    """
    return target.depth == source.depth - 1


def sort_key(identifier: Identifier) -> tuple[int, int]:
    """Layer depth, then number *numerically*. Sorting the rendered strings
    would put B·10 before B·09 and quietly scramble every spine."""
    return (identifier.depth, identifier.number)
