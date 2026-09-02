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

LAYER_NAMES = {
    "I": "intent",
    "B": "behaviour",
    "A": "architecture",
    "S": "spec",
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
        if self.layer not in LAYERS:
            raise InvalidIdentifier(
                f"unknown layer {self.layer!r}, expected one of {LAYERS}"
            )
        if self.number < 1:
            raise InvalidIdentifier(
                f"identifier numbers start at 1, got {self.number}"
            )

    def __str__(self) -> str:
        return f"{self.layer}{SEPARATOR}{self.number:0{_PAD}d}"

    @property
    def depth(self) -> int:
        """Position in LAYERS. Lower is closer to intent."""
        return LAYERS.index(self.layer)

    @property
    def layer_name(self) -> str:
        return LAYER_NAMES[self.layer]


def parse(text: str) -> Identifier:
    match = _PATTERN.match(text.strip() if isinstance(text, str) else "")
    if match is None:
        raise InvalidIdentifier(f"malformed identifier: {text!r}")
    return Identifier(layer=match.group(1), number=int(match.group(2)))


def is_upward(source: Identifier, target: Identifier) -> bool:
    """True when `target` sits strictly closer to intent than `source` --
    i.e. `source` deriving from `target` runs in the legal direction.

    Skipping layers is allowed: a behaviour may derive straight from intent,
    and a spec entry may serve an architecture entry two layers up. Demanding
    strictly adjacent layers would force filler entries that say nothing.
    """
    return target.depth < source.depth


def sort_key(identifier: Identifier) -> tuple[int, int]:
    """Layer depth, then number *numerically*. Sorting the rendered strings
    would put B·10 before B·09 and quietly scramble every spine."""
    return (identifier.depth, identifier.number)
