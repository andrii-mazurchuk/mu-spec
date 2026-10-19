"""/stats says which build is answering and which store it reads.

Several mu-spec instances ran at once -- the node container, a dev checkout,
stray servers on 9006/9007/9016 -- holding different projects, and sessions
verified against, wrote into, or ruled from the wrong one (cross-12).
Separately, pushed fixes sat undeployed unseen because the container carries
no git metadata, and the deployed version had to be inferred by grepping
(m1201-6, cross-10). Both answers now come from the process itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mu_spec.server import GIT_SHA_ENV, git_sha, handle
from mu_spec.storage import ProjectStore

SHA = "0123456789abcdef0123456789abcdef01234567"
REPO = Path(__file__).resolve().parent.parent


def _stats(tmp_path, **kw):
    store = ProjectStore(tmp_path / "projects")
    _, _, raw = handle("GET", "/stats", store, tmp_path, now_fn=lambda: 1.0, **kw)
    return store, json.loads(raw)["metrics"]


def test_stats_names_the_store_root(tmp_path):
    store, metrics = _stats(tmp_path, sha_fn=lambda: SHA)
    assert metrics["store_root"] == str(store.root().resolve())


def test_stats_names_the_deployed_sha(tmp_path):
    _, metrics = _stats(tmp_path, sha_fn=lambda: SHA)
    assert metrics["git_sha"] == SHA


def test_no_checkout_is_a_null_sha_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    assert git_sha(tmp_path) is None
    _, metrics = _stats(tmp_path, sha_fn=lambda: git_sha(tmp_path))
    assert metrics["git_sha"] is None


def test_a_malformed_git_dir_is_a_null_sha(tmp_path, monkeypatch):
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    (tmp_path / ".git").write_text("not a gitdir line", encoding="utf-8")
    assert git_sha(tmp_path) is None


def _repo(root: Path, head: str) -> Path:
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    return git


def test_the_sha_is_read_through_a_branch_ref(tmp_path, monkeypatch):
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    git = _repo(tmp_path, "ref: refs/heads/main")
    (git / "refs" / "heads" / "main").write_text(SHA + "\n", encoding="utf-8")
    assert git_sha(tmp_path) == SHA


def test_the_sha_is_read_from_packed_refs(tmp_path, monkeypatch):
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    git = _repo(tmp_path, "ref: refs/heads/main")
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{SHA} refs/heads/main\n", encoding="utf-8"
    )
    assert git_sha(tmp_path) == SHA


def test_a_detached_head_is_its_own_sha(tmp_path, monkeypatch):
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    _repo(tmp_path, SHA)
    assert git_sha(tmp_path) == SHA


def test_an_injected_sha_wins_where_there_is_no_checkout(tmp_path, monkeypatch):
    """The container has no .git, so the deploy says which build it is."""
    monkeypatch.setenv(GIT_SHA_ENV, SHA)
    assert git_sha(tmp_path) == SHA


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_sha_of_this_checkout_is_what_git_says(monkeypatch):
    """Driven against the real checkout -- a worktree when run from one."""
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    )
    if head.returncode != 0:
        pytest.skip("not a git checkout")
    assert git_sha(REPO) == head.stdout.strip()
