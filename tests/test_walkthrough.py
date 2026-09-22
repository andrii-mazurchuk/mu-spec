"""The walkthrough is documentation that executes, so it has to keep executing.

It drives the whole pipeline end to end -- request, propagation, slicing,
work package, issues, reconciliation, split, metrics -- through the same
`handle()` every caller uses. That makes it the only thing in this repo that
exercises the ordering *between* operations rather than one operation at a
time, and the suite had no view of it at all: a change that refused something
the walkthrough does broke it silently, because CI runs `pytest` and nothing
else.

That is not hypothetical. Requiring a slice to exist before an amendment may
name one broke the walkthrough at step 2 and nothing noticed, because every
test that would have caught it had been updated in the same commit.

Run as a subprocess rather than imported: it is a script, it parses argv and
calls sys.exit, and the point is to check that the thing a person actually
runs still runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "walkthrough.py"


@pytest.mark.skipif(not SCRIPT.exists(), reason="walkthrough.py is not present")
def test_the_walkthrough_runs_start_to_finish(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--keep", str(tmp_path / "state")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, (
        "the walkthrough failed:\n"
        + "\n".join(proc.stdout.splitlines()[-25:])
        + "\n--- stderr ---\n"
        + proc.stderr[-2000:]
    )
    # It is meant to be read top to bottom, so check it got to the bottom
    # rather than merely exiting zero.
    assert "traces back through an amendment" in proc.stdout


@pytest.mark.skipif(not SCRIPT.exists(), reason="walkthrough.py is not present")
def test_the_walkthrough_leaves_a_loadable_graph(tmp_path):
    """Whatever it built has to still open. The duplicate-identifier bug this
    test was written for did not fail the run -- it failed the next read."""
    from mu_spec.storage import ProjectStore

    state = tmp_path / "state"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--keep", str(state)],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    assert proc.returncode == 0

    store = ProjectStore(state)
    projects = store.list_projects()
    assert projects, "the walkthrough built no project"
    for project in projects:
        graph = store.load_graph(project)          # raises on a duplicate
        ids = [str(e.id) for e in graph.entries()]
        assert len(ids) == len(set(ids))
