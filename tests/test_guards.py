"""The suite cannot reach anything real.

Twice a seeding script with a hardcoded port wrote invented entries into the
live t-finance under state/demo, burning identifiers that stay burned because
the store is append-only (m1102-1, m1105-8). Four tests once made real calls
to api.github.com (m1201-13). And two processes bound the same port on
Windows, so a stale build kept answering and verification failed for reasons
that were not in the code (m1102-18). tests/conftest.py makes the first two
impossible inside the suite; these tests prove the guards are on.
"""

from __future__ import annotations

import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import REAL_STATE
from mu_spec import main as entry
from mu_spec import server
from mu_spec.storage import ProjectStore


def test_a_test_cannot_open_a_socket():
    with pytest.raises(RuntimeError, match="real socket"):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)


@pytest.mark.parametrize("state", REAL_STATE)
def test_a_test_cannot_write_under_the_real_state_dir(state):
    # A directory that does not exist, so that without the guard this fails
    # on the missing parent rather than writing anything real.
    probe = state / "demo" / "__guard_probe__" / "x.jsonl"
    with pytest.raises(AssertionError, match="real state"):
        probe.write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError, match="real state"):
        open(probe, "a")


def test_reading_the_real_state_dir_is_still_allowed():
    for state in REAL_STATE:
        with pytest.raises(FileNotFoundError):
            open(state / "__guard_probe__", "r")


@pytest.mark.parametrize("state", REAL_STATE)
def test_the_store_refuses_the_real_state_dir_under_tests(state):
    with pytest.raises(ValueError, match="refuses"):
        ProjectStore(state / "projects")
    with pytest.raises(ValueError, match="refuses"):
        ProjectStore(state / "demo")


def test_the_store_accepts_any_other_root(tmp_path):
    ProjectStore(tmp_path / "projects").create_project("m")


@pytest.mark.real_socket
def test_the_server_refuses_a_port_another_server_holds(tmp_path):
    """The stale server is a stock ThreadingHTTPServer, which sets
    SO_REUSEADDR -- and on Windows that let a second process bind the same
    port and split the requests between two builds."""
    stale = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        port = stale.server_address[1]
        with pytest.raises(OSError):
            server.make_server("127.0.0.1", port, ProjectStore(tmp_path), tmp_path)
    finally:
        stale.server_close()


def test_main_exits_non_zero_when_it_cannot_bind(monkeypatch, tmp_path, capsys):
    def taken(*_):
        raise OSError("address already in use")

    monkeypatch.setattr(entry, "serve", taken)
    assert entry.main(["--port", "9006", "--root", str(tmp_path)]) != 0
    assert "9006" in capsys.readouterr().err
