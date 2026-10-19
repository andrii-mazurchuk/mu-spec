"""One layer set, consumed by every surface.

When the test layer arrived, the planner, slice context, issue routing, the
scorer and the dashboard each held their own hand-written list of layers, and
only some of them were updated. Issues against a test escalated as UNOWNED
(iss-0154, against T-94, fell to a human), the scorer reported every scenario
as homeless, and the dashboard dropped tests silently. The fix is structural:
the layers are named once, in `identifiers`, and these tests fail when a
module spells one out by hand or when a kind of entry reaches a surface with
nowhere to go.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import (
    ALL_LAYERS,
    IMPLEMENTED,
    INTENT,
    LAYER_NAMES,
    UNSLICED,
    Identifier,
)
from mu_spec.issues import SEMANTIC, Issue
from mu_spec.reconcile import UNOWNED, route
from mu_spec.storage import Manifest, ProjectStore, Slice
from mu_spec.units import project

PACKAGE = Path(__file__).resolve().parent.parent / "mu_spec"


def _strings(path: Path):
    """Every string constant in a module that is not a docstring. Comments and
    docstrings may name a layer; code may not."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def _chain() -> list[Entry]:
    """One entry of every kind there is, each deriving from the one above."""
    entries, parent = [], None
    for layer in ALL_LAYERS:
        ident = Identifier(layer, 1)
        entries.append(Entry(id=ident, derives_from=(parent,) if parent else (), title=layer))
        parent = ident
    return entries


# -- (a) one constant -------------------------------------------------------


def test_every_layer_is_named_and_classified():
    assert tuple(LAYER_NAMES) == ALL_LAYERS
    assert set(UNSLICED) <= set(ALL_LAYERS)
    assert set(IMPLEMENTED) <= set(ALL_LAYERS)


def test_no_module_spells_a_layer_out_by_hand():
    """A bare `"S"` or `("I", "B")` in code is a second copy of the layer set,
    and the copy is the one that misses the next layer."""
    found = [
        f"{path.name}:{line}: {value!r}"
        for path in sorted(PACKAGE.glob("*.py"))
        if path.name != "identifiers.py"
        for line, value in _strings(path)
        if value in ALL_LAYERS
    ]
    assert found == []


def test_the_dashboard_columns_are_the_layer_set_plus_modules():
    """The page is JavaScript and cannot import the constant, so it is pinned
    to it instead: a new layer fails here until the page draws it."""
    from mu_spec import dashboard

    page = dashboard.read_page()
    layers = re.search(r"const LAYERS=\[(.*?)\];", page, re.S).group(1)
    assert re.findall(r'\["([A-Z])"', layers) == [*ALL_LAYERS, "M"]


# -- (b) every kind resolves to an owner and a plan --------------------------


def test_every_entry_kind_resolves_to_an_owner():
    """iss-0154 against T-94 escalated as unowned because a test takes no
    slice and the router knew only slice membership. Every kind must route to
    a repair batch, except intent, which is never sliced and whose issues are
    a question for whoever asked -- by design, and asserted as such."""
    entries = _chain()
    sliced = {e.id for e in entries if e.id.layer not in UNSLICED}
    manifest = Manifest(project="m", slices={"core": Slice(name="core", members=sliced)})
    issues = [
        Issue(id=f"iss-{e.id}", project="m", target=str(e.id), target_slice=None,
              raised_by=None, kind=SEMANTIC)
        for e in entries
    ]

    batches, escalations = route(manifest, Graph(entries), issues)

    routed = {i.target for b in batches for i in b.issues}
    escalated = {e.issue: e.reason for e in escalations}
    for e in entries:
        if e.id.layer == INTENT:
            assert escalated == {f"iss-{e.id}": UNOWNED}
        else:
            assert str(e.id) in routed, f"{e.id.layer_name} has no owner"


@pytest.mark.parametrize("layer", ALL_LAYERS)
def test_every_entry_kind_resolves_to_a_plan(tmp_path, layer):
    """A kind a module may implement is planned as a work unit; any other kind
    is refused as a backlink, so it is derived downward rather than left with
    no plan at all."""
    entries = _chain()
    target = Identifier(layer, 1)
    if layer in IMPLEMENTED:
        manifest = Manifest(project="m", modules={"x.py": {target}})
        units = project(manifest, Graph(entries)).units
        assert any(target in u.entries for u in units), f"{target} in no work unit"
    else:
        store = ProjectStore(tmp_path)
        store.create_project("m")
        for e in entries:
            store.append("m", [e], slice_name=None if e.id.layer in UNSLICED else "core")
        with pytest.raises(ValueError, match="implements"):
            store.set_module("m", "x.py", [str(target)])


# -- (c) separator and suffix literals live in two modules -------------------

# Spelling a separator or a unit-key suffix anywhere else is what broke four
# things at once when the separator changed (muspec-ship-07): a sniff for the
# middle dot refused every amendment, the route regex 404'd ASCII ids, stored
# cut keys reported 144 units changed, and the emission log nearly duplicated
# every issue. Code asks identifiers.py and units.py instead.
_LITERAL = re.compile(r"(?<![A-Za-z])-T(?![a-z])|:T(?![a-z])|·")

# Prose a model reads, not logic. Each entry is (module, a substring of the
# string that is allowed to carry the literal) and says why.
_ALLOWED = (
    # Tool descriptions show a caller what a test unit's key looks like.
    ("server.py", "One work unit and everything needed to build it"),
    ("server.py", "A unit key as the listing reports it"),
)


def test_no_separator_or_suffix_literal_outside_the_identifier_modules():
    found = [
        f"{path.name}:{line}: {value[:60]!r}"
        for path in sorted(PACKAGE.glob("*.py"))
        if path.name not in ("identifiers.py", "units.py")
        for line, value in _strings(path)
        if _LITERAL.search(value)
        and not any(path.name == name and part in value for name, part in _ALLOWED)
    ]
    assert found == []
