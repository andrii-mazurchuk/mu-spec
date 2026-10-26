"""A preview's `would_ship` renders only the units an amendment can move.

Rendering every shipped unit twice outran the MCP bridge's timeout on DARK
(~150 shipped units), so a dry run could not be asked at all. The scoped
computation must answer exactly what the full one does; the full one is kept
here, and only here, as the oracle.
"""

from __future__ import annotations

import json

import pytest

from mu_spec import service
from mu_spec.graph import Entry
from mu_spec.identifiers import parse
from mu_spec.server import handle
from mu_spec.storage import ProjectStore
from test_sync import Tracker


@pytest.fixture()
def shipped(tmp_path, monkeypatch):
    """I-01 <- B-01 <- A-01 <- S-01..S-04, each in its own module, S-03's body
    citing S-01; T-01 <- S-01, T-02 <- S-02. Cut and emitted: every unit has
    an issue."""
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    store = ProjectStore(tmp_path / "projects")
    store.create_project("p")
    store.append("p", [Entry(id=parse("I-01"), title="intent")])
    store.append("p", [Entry(id=parse("B-01"), derives_from=(parse("I-01"),),
                             title="behaviour")], slice_name="core")
    store.append("p", [Entry(id=parse("A-01"), derives_from=(parse("B-01"),),
                             title="arch")], slice_name="core")
    bodies = {"01": "build it", "02": "build two", "03": "call S-01 to charge",
              "04": "build four"}
    for n, body in bodies.items():
        store.append("p", [Entry(id=parse(f"S-{n}"), derives_from=(parse("A-01"),),
                                 title=f"contract {n}", body=body)], slice_name="core")
        store.set_module("p", f"app/m{n}.py", [f"S-{n}"])
    for n in ("01", "02"):
        store.append("p", [Entry(id=parse(f"T-{n}"), derives_from=(parse(f"S-{n}"),),
                                 title=f"scenario {n}", body="it fails")], slice_name="core")
    store.set_module("p", "tests/test_m.py", ["T-01", "T-02"])
    for layer, top in (("I", 1), ("B", 1), ("A", 1), ("S", 4), ("T", 2)):
        for _ in range(top):
            store.allocate("p", layer)
    store.set_repo("p", "o/dark")
    gh = Tracker()

    def call(method, path, body=None):
        status, _, raw = handle(method, path, store, tmp_path, body,
                                now_fn=lambda: 5.0, emit_client=gh)
        return status, json.loads(raw)

    assert call("POST", "/projects/p/units/cut", {})[0] == 200
    assert call("POST", "/projects/p/emit", {})[0] == 200
    _, issue = call("POST", "/projects/p/issues",
                    {"target": "S-01", "kind": "semantic", "claim": "wrong"})
    return call, issue["id"]


def S(n, body, **kw):
    return {"layer": "S", "title": f"contract {n}", "body": body,
            "derives_from": ["A-01"], "supersedes": f"S-{n}",
            "preserves_interface": True, **kw}


AMENDMENTS = {
    # S body change, and S-03's ticket cites S-01: its contract is rewritten.
    "s_body": ("core", [S("01", "build it better")]),
    # The same text under a new id: a renumber, moved only by citation.
    "s_renumber": ("core", [S("01", "build it")]),
    # A spec entry nothing cites.
    "s_uncited": ("core", [S("04", "build four, faster")]),
    # Edge-only: the same contract, a new horizontal edge.
    "edge_only": ("core", [S("02", "build two", depends_on=["S-04"])]),
    # A scenario changes: the test unit moves.
    "t_change": (None, [{"layer": "T", "title": "scenario 01", "body": "it fails loudly",
                           "derives_from": ["S-01"], "supersedes": "T-01",
                           "preserves_interface": True}]),
    # A new scenario joins an existing test unit.
    "t_added": (None, [{"layer": "T", "title": "scenario 01b", "body": "and again",
                          "derives_from": ["S-01"]}]),
    # Two at once.
    "two": ("core", [S("01", "build it better"), S("02", "build two better")]),
}


@pytest.mark.parametrize("name", sorted(AMENDMENTS))
def test_scoped_would_ship_equals_rendering_every_unit(shipped, monkeypatch, name):
    call, issue = shipped
    slice_name, entries = AMENDMENTS[name]
    if entries[0]["layer"] == "T":
        issue = call("POST", "/projects/p/issues",
                     {"target": "T-01", "kind": "semantic", "claim": "wrong"})[1]["id"]
    body = {"in_response_to": issue, "dry_run": True, "entries": entries,
            **({"slice": slice_name} if slice_name else {})}

    status, fast = call("POST", "/projects/p/amendments", body)
    assert status == 200 and fast["admitted"], fast

    rendered = []
    real = service._contracts
    monkeypatch.setattr(service, "_contracts",
                        lambda *a, keys=None, **kw: rendered.append(keys) or real(*a, keys=keys, **kw))
    monkeypatch.setattr(service, "_movable", lambda before, after: None)  # every key
    status, full = call("POST", "/projects/p/amendments", body)
    assert status == 200 and full["admitted"], full
    assert rendered and all(k is None for k in rendered)

    assert fast["would_ship"] == full["would_ship"]


def test_the_scoped_preview_renders_fewer_units_than_are_shipped(shipped, monkeypatch):
    call, issue = shipped
    rendered = []
    real = service._contracts
    monkeypatch.setattr(service, "_contracts",
                        lambda *a, keys=None, **kw: rendered.append(keys) or real(*a, keys=keys, **kw))
    _, out = call("POST", "/projects/p/amendments",
                  {"in_response_to": issue, "slice": "core", "dry_run": True,
                   "entries": [S("04", "build four, faster")]})
    assert [u["key"] for u in out["would_ship"]] == ["S-04"]
    assert rendered and all(set(k) == {"S-04"} for k in rendered), rendered
