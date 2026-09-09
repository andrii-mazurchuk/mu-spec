"""Where mu-spec puts the derivation graph.

The unit keeps all private state under one directory, learned from
HOLONIC_STATE_DIR -- see holonic-node/docs/UNIT_STANDARDS.md, "Private
storage". The graph is append-only and identifier-stable, so writing it
to the wrong directory is not a recoverable mistake: the unit would come
up healthy, serving an empty project list, while the real graph sat
somewhere nobody reads. Hence pinning the resolution order here.
"""

from __future__ import annotations

from pathlib import Path

from mu_spec.main import PROJECTS_SUBDIR, build_parser, default_root, state_dir


def test_state_dir_comes_from_the_injected_environment(monkeypatch):
    monkeypatch.setenv("HOLONIC_STATE_DIR", "/var/lib/holonic/mu-spec")
    assert state_dir() == Path("/var/lib/holonic/mu-spec")


def test_state_dir_falls_back_so_the_unit_runs_without_a_gateway(monkeypatch):
    monkeypatch.delenv("HOLONIC_STATE_DIR", raising=False)
    assert state_dir() == Path("state")


def test_projects_live_inside_the_state_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOLONIC_STATE_DIR", str(tmp_path))
    assert default_root() == tmp_path / PROJECTS_SUBDIR


def test_the_default_is_resolved_at_call_time_not_import_time(monkeypatch, tmp_path):
    monkeypatch.setenv("HOLONIC_STATE_DIR", str(tmp_path / "first"))
    first = default_root()
    monkeypatch.setenv("HOLONIC_STATE_DIR", str(tmp_path / "second"))
    assert default_root() != first


def test_root_is_unset_by_default_so_main_can_resolve_it():
    assert build_parser().parse_args([]).root is None


def test_an_explicit_root_still_wins():
    args = build_parser().parse_args(["--root", "/elsewhere/projects"])
    assert args.root == "/elsewhere/projects"
