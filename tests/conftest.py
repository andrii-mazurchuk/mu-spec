"""Guards every test runs under: no real socket, no write into real state.

The real state directory holds live projects (state/demo is Andrey's
t-finance) and is append-only, so a test that writes there burns identifiers
for good. A test that needs a listening socket says so with
`@pytest.mark.real_socket`; nothing may opt out of the state guard.
"""

from __future__ import annotations

import builtins
import io
import os
import socket
from pathlib import Path

import pytest

from mu_spec.storage import REFUSE_ROOTS_ENV

REPO = Path(__file__).resolve().parent.parent


def _checkouts():
    yield REPO
    git = REPO / ".git"
    if git.is_file():
        # A worktree: `.git` names <main>/.git/worktrees/<name>, and the real
        # state sits beside the main checkout, not this one.
        yield Path(git.read_text(encoding="utf-8").split(":", 1)[1].strip()).parents[2]


REAL_STATE = tuple(dict.fromkeys((c / "state").resolve() for c in _checkouts()))


def _is_real(path) -> bool:
    try:
        target = Path(os.fsdecode(path)).resolve()
    except (TypeError, ValueError):
        return False
    return any(target.is_relative_to(state) for state in REAL_STATE)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_socket: the test binds a real socket")


@pytest.fixture(autouse=True)
def _no_reaching_out(request, monkeypatch):
    monkeypatch.setenv(REFUSE_ROOTS_ENV, os.pathsep.join(map(str, REAL_STATE)))

    real_open, real_mkdir = io.open, os.mkdir

    def guarded_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and set(mode) & set("wax+") and _is_real(file):
            raise AssertionError(f"a test wrote into the real state dir: {file}")
        return real_open(file, mode, *args, **kwargs)

    def guarded_mkdir(path, *args, **kwargs):
        if _is_real(path):
            raise AssertionError(f"a test wrote into the real state dir: {path}")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "mkdir", guarded_mkdir)

    if request.node.get_closest_marker("real_socket") is None:
        def refused(*_args, **_kwargs):
            raise RuntimeError("a test opened a real socket; inject the transport")

        for name in ("connect", "connect_ex", "bind"):
            monkeypatch.setattr(socket.socket, name, refused)
