"""M11: what an amendment WOULD do, asked before it is committed.

`dry_run: true` on submit_amendment runs the real path against a scratch copy
of the project and returns its answer: the same result a commit would give,
refusals included, with nothing written and no identifier allocated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mu_spec.graph import Entry
from mu_spec.identifiers import parse
from mu_spec.lifecycle import Lifecycle
from mu_spec.server import _tools, handle
from mu_spec.storage import ProjectStore, Slice
from test_sync import Tracker


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture()
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    return d


def call(store, prompts, method, path, body=None):
    status, _, raw = handle(method, path, store, prompts, body, now_fn=lambda: 7.0)
    return status, json.loads(raw)


def amend(store, prompts, mid, entries, **kw):
    return call(store, prompts, "POST", "/projects/m/amendments",
                {"in_response_to": mid, "entries": entries, **kw})


@pytest.fixture()
def mid(store, prompts):
    """I-01 <- B-01 <- A-01 <- S-01, S-02 (depends_on S-01); T-01 <- S-01,
    T-02 <- S-02. Modules: core.py S-01 + S-02, test_core.py T-01 + T-02."""
    _, msg = call(store, prompts, "POST", "/inbox",
                  {"type": "initiate", "title": "a shop", "project": "m"})
    m = msg["message_id"]
    call(store, prompts, "POST", "/projects", {"project": "m", "in_response_to": m})
    for entry in ({"layer": "I", "title": "buyers buy"},
                  {"layer": "B", "title": "a buyer pays", "derives_from": ["I-01"]}):
        assert amend(store, prompts, m, [entry])[1]["admitted"]
    manifest = store.load_manifest("m")
    manifest.slices["s"] = Slice(name="s")
    store.save_manifest("m", manifest)
    for entries in (
        [{"layer": "A", "title": "a ledger", "derives_from": ["B-01"]}],
        [{"layer": "S", "title": "charge", "derives_from": ["A-01"]},
         {"layer": "S", "title": "refund", "derives_from": ["A-01"],
          "depends_on": ["#0"]}],
    ):
        assert amend(store, prompts, m, entries, slice="s")[1]["admitted"]
    assert amend(store, prompts, m, [
        {"layer": "T", "title": "charge fails", "derives_from": ["S-01"]},
        {"layer": "T", "title": "refund fails", "derives_from": ["S-02"]},
    ])[1]["admitted"]
    store.set_module("m", "core.py", ["S-01", "S-02"])
    store.set_module("m", "test_core.py", ["T-01", "T-02"])
    return m


SUPERSEDE = [{"layer": "S", "title": "charge in a currency", "derives_from": ["A-01"],
              "supersedes": "S-01"}]


def footprint(root: Path) -> dict[str, bytes]:
    """Every byte of the store but the lifecycle log, which records the POST
    itself as a `mutation` -- an audit of the call, not a write to the graph."""
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and p.name != "lifecycle.jsonl"}


def test_a_preview_answers_what_the_commit_then_does_and_writes_nothing(store, prompts, mid):
    root = store.root()
    before = footprint(root)
    status, preview = amend(store, prompts, mid, SUPERSEDE, slice="s", dry_run=True)
    assert status == 200, preview
    assert footprint(root) == before, "a preview wrote to the store"
    assert preview["dry_run"] is True and preview["admitted"] is True
    assert preview["created"] == ["S-03"]
    assert preview["restate_or_keep"] == ["T-01"]
    assert preview["dependents_stranded"] == ["S-02"]
    assert preview["tests_of_dependents"] == ["T-02"]
    assert preview["repointed_modules"] == [
        {"module": "core.py", "was": ["S-01", "S-02"], "now": ["S-02", "S-03"]}]
    assert not preview["gates"]["sound"]

    status, real = amend(store, prompts, mid, SUPERSEDE, slice="s")
    assert status == 200
    # The same path ran: the commit says what the preview said.
    assert {k: v for k, v in preview.items() if k not in ("dry_run", "would_ship")} == real


def test_a_preview_records_no_lifecycle_event_but_the_mutation(store, prompts, mid):
    events = Lifecycle(store.events_path())
    before = len(events.list("m"))
    amend(store, prompts, mid, SUPERSEDE, slice="s", dry_run=True)
    new = events.list("m")[before:]
    assert [e.kind for e in new] == ["mutation"]


def test_the_preview_of_a_refused_amendment_shows_the_refusal(store, prompts, mid):
    before = footprint(store.root())
    status, out = amend(store, prompts, mid,
                        [{"layer": "S", "title": "x", "derives_from": ["A-01"],
                          "depends_on": ["S-99"]}], slice="s", dry_run=True)
    assert status == 200, out
    assert out["dry_run"] is True and out["admitted"] is False
    assert out["findings"] and out["findings"][0]["kind"] == "bad_dependency"
    assert footprint(store.root()) == before


def test_a_preview_of_a_malformed_amendment_reports_the_reason_it_would_get(store, prompts, mid):
    status, out = amend(store, prompts, mid,
                        [{"layer": "S", "title": "x", "derives_from": ["A-01"]},
                         {"layer": "T", "title": "y", "derives_from": ["S-01"]}],
                        slice="s", dry_run=True)
    assert status == 200, out
    assert out["admitted"] is False and "one layer" in out["reason"]
    # And the commit is refused the same way.
    assert amend(store, prompts, mid,
                 [{"layer": "S", "title": "x", "derives_from": ["A-01"]},
                  {"layer": "T", "title": "y", "derives_from": ["S-01"]}],
                 slice="s")[0] == 400


def test_a_preview_names_the_shipped_units_whose_contract_would_move(tmp_path, monkeypatch):
    monkeypatch.setenv("MU_SPEC_GITHUB_TOKEN", "tok")
    store = ProjectStore(tmp_path / "projects")
    store.create_project("p")
    store.append("p", [Entry(id=parse("I-01"), title="intent")])
    store.append("p", [Entry(id=parse("B-01"), derives_from=(parse("I-01"),),
                             title="behaviour")], slice_name="core")
    store.append("p", [Entry(id=parse("A-01"), derives_from=(parse("B-01"),),
                             title="arch")], slice_name="core")
    for n in ("01", "02"):
        store.append("p", [Entry(id=parse(f"S-{n}"), derives_from=(parse("A-01"),),
                                 title=f"contract {n}", body="build it")], slice_name="core")
        store.set_module("p", f"app/m{n}.py", [f"S-{n}"])
    for layer, top in (("I", 1), ("B", 1), ("A", 1), ("S", 2)):
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
    before = footprint(store.root())
    status, out = call("POST", "/projects/p/amendments", {
        "in_response_to": issue["id"], "slice": "core", "dry_run": True,
        "entries": [{"layer": "S", "title": "contract 01", "body": "build it better",
                     "derives_from": ["A-01"], "supersedes": "S-01",
                     "preserves_interface": True}]})
    assert status == 200 and out["admitted"], out
    assert footprint(store.root()) == before
    moved = {u["key"]: u for u in out["would_ship"]}
    assert set(moved) == {"S-01"}, out["would_ship"]
    assert moved["S-01"]["ship"] == "edit" and moved["S-01"]["number"]


def test_the_s325_oracle_reaches_t571_and_t554():
    """DARK's S-325 (superseding S-316) broke fixtures of T-571 and T-554,
    tests of entries that depend on S-316 transitively (S-309 directly,
    S-308 through S-309). Read-only replay of the retrospective snapshot."""
    snapshot = Path(r"C:\agents\teams\forge\analysis\dark-retro\snapshot\node")
    if not snapshot.exists():
        pytest.skip("the DARK snapshot is not on this machine")
    from replay import harness
    from mu_spec.service import tests_of_dependents

    history = harness.load(snapshot)
    graph, _ = harness.build(history, history.born["S-325"] - 0.001)
    found = tests_of_dependents(graph, [parse("S-316")])
    assert {"T-571", "T-554"} <= set(found)


def test_tools_offer_the_preview_on_submit_amendment():
    tool = next(t for t in _tools() if t["name"] == "submit_amendment")
    assert tool["input_schema"]["properties"]["dry_run"] == {"type": "boolean"}
    assert "dry_run" in tool["description"]
