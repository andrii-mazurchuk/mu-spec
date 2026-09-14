"""The dashboard page, read from wherever this package actually lives.

Page tier, not spec tier: the node renders a spec with its own panel
components, and none of them draw a derivation graph. A five-column
layered graph with same-layer dependency edges is the one thing this
unit has worth looking at, so this is the escape hatch the standard
keeps for exactly that -- and taking it means owning the whole cost of
looking like the rest of the console, which is what the token contract
in the page's `<head>` is for.

`importlib.resources` rather than `Path(__file__).parent`: the point of
the standard's rule is that the page is there *as installed*, and a
checkout-relative path proves only that the file is in the checkout,
which is true whether or not the packaging ships it.
"""

from __future__ import annotations

from importlib import resources

PAGE = "dashboard.html"


def read_page() -> str | None:
    """The page, or None if it did not survive packaging.

    A missing asset degrades to 404, which the node reads as "this unit
    has no dashboard" -- the same answer a unit that never wrote one
    gives. That is a real failure mode wearing the appearance of a
    deliberate choice, which is why `tests/test_dashboard.py` asserts
    this returns something.
    """
    try:
        return resources.files(__package__).joinpath(PAGE).read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError, FileNotFoundError):
        return None
