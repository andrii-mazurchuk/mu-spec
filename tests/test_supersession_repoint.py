"""M14: a supersession re-points what is mechanical in the same write.

Module declarations are this unit's own manifest, so a module implementing
a retired spec entry is moved onto its replacement in the write that
retires it. Tests deriving from it and dependents of it are NOT rewritten --
whether they still hold is judgement -- they are listed in the response.
"""

from __future__ import annotations

import json
import threading

import pytest

from mu_spec import lifecycle as lc
from mu_spec.identifiers import parse
from mu_spec.lifecycle import Lifecycle
from mu_spec.server import handle
from mu_spec.storage import ProjectStore, Slice


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
    """I-01 <- B-01 <- A-01 <- S-01, S-02 (depends_on S-01); T-01 <- S-01.
    Modules: core.py implements S-01 and S-02, extra.py S-01, test_core.py
    T-01."""
    _, msg = call(store, prompts, "POST", "/inbox",
                  {"type": "initiate", "title": "a shop", "project": "m"})
    m = msg["message_id"]
    call(store, prompts, "POST", "/projects", {"project": "m", "in_response_to": m})
    for entry in ({"layer": "I", "title": "buyers buy"},
                  {"layer": "B", "title": "a buyer pays", "derives_from": ["I-01"]}):
        status, out = amend(store, prompts, m, [entry])
        assert status == 200 and out["admitted"], out
    manifest = store.load_manifest("m")
    manifest.slices["s"] = Slice(name="s")
    store.save_manifest("m", manifest)
    for entries in (
        [{"layer": "A", "title": "a ledger", "derives_from": ["B-01"]}],
        [{"layer": "S", "title": "charge", "derives_from": ["A-01"]},
         {"layer": "S", "title": "refund", "derives_from": ["A-01"],
          "depends_on": ["#0"]}],
    ):
        status, out = amend(store, prompts, m, entries, slice="s")
        assert status == 200 and out["admitted"], out
    status, out = amend(store, prompts, m,
                        [{"layer": "T", "title": "charge fails", "derives_from": ["S-01"]}])
    assert status == 200 and out["admitted"], out
    store.set_module("m", "core.py", ["S-01", "S-02"])
    store.set_module("m", "extra.py", ["S-01"])
    store.set_module("m", "test_core.py", ["T-01"])
    return m


def supersede(store, prompts, mid, preserves=False):
    entry = {"layer": "S", "title": "charge in a currency", "derives_from": ["A-01"],
             "supersedes": "S-01"}
    if preserves:
        entry["preserves_interface"] = True
    return amend(store, prompts, mid, [entry], slice="s")


def modules(store):
    return {p: sorted(str(i) for i in ids)
            for p, ids in store.load_manifest("m").modules.items()}


@pytest.mark.parametrize("preserves", [False, True])
def test_modules_implementing_the_retired_entry_move_to_its_replacement(
        store, prompts, mid, preserves):
    status, out = supersede(store, prompts, mid, preserves)
    assert status == 200 and out["admitted"], out
    assert modules(store) == {"core.py": ["S-02", "S-03"], "extra.py": ["S-03"],
                              "test_core.py": ["T-01"]}
    assert out["repointed_modules"] == [
        {"module": "core.py", "was": ["S-01", "S-02"], "now": ["S-02", "S-03"]},
        {"module": "extra.py", "was": ["S-01"], "now": ["S-03"]},
    ]


@pytest.mark.parametrize("preserves", [False, True])
def test_tests_of_the_retired_entry_are_listed_and_left_alone(
        store, prompts, mid, preserves):
    before = store.load_graph("m").get(parse("T-01"))
    _, out = supersede(store, prompts, mid, preserves)
    assert out["restate_or_keep"] == ["T-01"]
    raw = {e.id: e for e in store.load_all("m")}
    assert raw[parse("T-01")] == before
    assert raw[parse("T-01")].derives_from == (parse("S-01"),)


def test_a_non_preserving_supersession_lists_its_stranded_dependents(store, prompts, mid):
    _, out = supersede(store, prompts, mid)
    assert out["dependents_stranded"] == ["S-02"]
    assert {r["id"] for r in out["stale_references"]} == {"S-02", "T-01"}
    raw = {e.id: e for e in store.load_all("m")}
    assert raw[parse("S-02")].depends_on == (parse("S-01"),)


def test_a_preserving_supersession_strands_no_dependent(store, prompts, mid):
    _, out = supersede(store, prompts, mid, preserves=True)
    assert out["dependents_stranded"] == []
    assert store.load_graph("m").get(parse("S-02")).depends_on == (parse("S-03"),)


def test_the_change_log_carries_the_modules_before_and_after(store, prompts, mid):
    supersede(store, prompts, mid)
    row = Lifecycle(store.events_path()).list(kind=lc.MUTATION)[-1]
    assert row.facts["route"] == "amendment"
    assert row.facts["was"] == {"modules": {"core.py": ["S-01", "S-02"],
                                            "extra.py": ["S-01"]}}
    assert row.facts["result"]["repointed_modules"][1]["now"] == ["S-03"]


def test_a_failed_manifest_write_rolls_forward_on_the_next_load(
        store, prompts, mid, monkeypatch, tmp_path):
    """The entries landed, the manifest did not: the journal written before
    either says what the manifest must become, and the entries prove the
    amendment happened -- so the next reader completes it."""
    real = ProjectStore._write_manifest

    def boom(self, path, manifest):
        if parse("S-03") in manifest.slices["s"].members:  # append's, not allocate's
            raise OSError("disk went away")
        real(self, path, manifest)

    monkeypatch.setattr(ProjectStore, "_write_manifest", boom)
    with pytest.raises(OSError):
        supersede(store, prompts, mid)
    monkeypatch.setattr(ProjectStore, "_write_manifest", real)

    fresh = ProjectStore(tmp_path / "projects")
    assert parse("S-03") in {e.id for e in fresh.load_all("m")}
    manifest = fresh.load_manifest("m")
    assert manifest.implementers(parse("S-03")) == ("core.py", "extra.py")
    assert parse("S-03") in manifest.slices["s"].members
    assert not list((tmp_path / "projects" / "m").glob("*.pending*"))


def test_a_failed_entry_write_rolls_back_on_the_next_load(
        store, prompts, mid, monkeypatch, tmp_path):
    """Nothing landed, so the journal is discarded and the old world stands."""
    from pathlib import Path

    real = Path.write_text

    def boom(self, *a, **kw):
        if self.suffix == ".jsonl":
            raise OSError("disk went away")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError):
        supersede(store, prompts, mid)
    monkeypatch.setattr(Path, "write_text", real)

    fresh = ProjectStore(tmp_path / "projects")
    assert parse("S-03") not in {e.id for e in fresh.load_all("m")}
    assert fresh.load_manifest("m").implementers(parse("S-01")) == ("core.py", "extra.py")
    assert not list((tmp_path / "projects" / "m").glob("*.pending*"))


def test_a_concurrent_cut_waits_for_the_supersession(store, prompts, mid, monkeypatch):
    """A cut taken while the amendment is writing must see the old world or
    the new one. It is started between the entry write and the manifest
    write, and must not finish before the manifest lands."""
    real = ProjectStore._write_manifest
    cut: dict = {}
    started = []

    def slow(self, path, manifest):
        if not started and parse("S-03") in manifest.slices["s"].members:
            t = threading.Thread(target=lambda: cut.update(
                call(store, prompts, "POST", "/projects/m/units/cut", {})[1]))
            t.start()
            started.append(t)
            t.join(0.3)
            assert t.is_alive(), "the cut ran inside the half-applied write"
        real(self, path, manifest)

    monkeypatch.setattr(ProjectStore, "_write_manifest", slow)
    status, out = supersede(store, prompts, mid)
    assert status == 200 and out["admitted"], out
    started[0].join(5)
    # Unsound (S-02 and T-01 are stranded), so refused -- but refused on the
    # new world, which is the point: it saw S-03 and its stranding.
    assert cut["cut"] is False
    stranded = {f["id"] for f in cut["gates"]["findings"]}
    assert {"S-02", "T-01"} <= stranded
