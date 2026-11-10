"""/stats is counts only, so it answers fast at any store size.

The node's proxy gives up at 5s, and the first /stats after a container
recreate took longer: it ran every gate and a wave schedule on every project.
Those aggregates live at /stats/structure now (Andrey, 2026-11-10).
"""

from __future__ import annotations

from mu_spec import service
from mu_spec.inbox import Inbox
from test_server import call, post, prompts, seed, store  # noqa: F401  (fixtures)
from mu_spec.server import handle

MOVED = {"projects_sound", "projects_complete", "max_wave_depth", "chain_projects"}


def test_stats_keeps_the_counts_and_drops_the_structure(store, prompts):
    seed(store, prompts)
    _, out = call(store, prompts, "GET", "/stats")
    metrics = out["metrics"]
    assert not MOVED & set(metrics)
    assert metrics["projects"] == 1 and metrics["entries"] == 4
    for key in ("unimplemented_spec", "open_issues", "semantic_issues",
                "change_locality_mean", "corrections_by_layer", "git_sha"):
        assert key in metrics


def test_stats_runs_no_gate_and_no_schedule(store, prompts, monkeypatch):
    seed(store, prompts)

    def boom(*_a, **_k):
        raise AssertionError("/stats must not run this")

    monkeypatch.setattr(service, "_gate_report", boom)
    monkeypatch.setattr(service, "schedule", boom)
    status, _ = call(store, prompts, "GET", "/stats")
    assert status == 200


def test_stats_reads_the_inbox_once_however_many_projects(store, prompts):
    seed(store, prompts)
    _, msg = post(store, prompts, "initiate", "another", project="n")
    call(store, prompts, "POST", "/projects", {"project": "n", "in_response_to": msg["message_id"]})
    reads = []

    class Counting(Inbox):
        def _read(self):
            reads.append(1)
            return super()._read()

    handle("GET", "/stats", store, prompts, None, now_fn=lambda: 1.0,
           inbox=Counting(store.inbox_path()))
    assert len(reads) == 1


def test_structure_carries_what_left_stats(store, prompts):
    seed(store, prompts)
    status, out = call(store, prompts, "GET", "/stats/structure")
    assert status == 200
    assert set(out) == {"unit", "computed_at", "metrics"}
    assert set(out["metrics"]) == MOVED | {"projects"}
    assert out["metrics"]["projects"] == 1
    assert out["metrics"]["projects_sound"] == 1
