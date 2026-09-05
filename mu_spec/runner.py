"""Launching one session, and the lock that stops two loops overlapping.

A session type is a directory containing a `CLAUDE.md`. Claude Code loads
that file natively once the process starts with the directory as `cwd`, so
nothing here assembles the static half of a prompt -- only the dynamic,
per-run half goes over `-p`. Discovery reads the filesystem rather than a
manifest, because a manifest is one more thing that can disagree with what
is actually on disk.

`spawn` is injected. The default shells out to the `claude` CLI; the suite
passes a fake and never spends an API call. That seam is the only reason
this module can be tested at all.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator, Sequence

from .dispatch import BACK_CHECK, DERIVATION, Dispatch, SLICING, TRIAGE

# A session type's model, when it wants one. Absent means "leave the CLI's
# own default in effect" -- which is not the same as passing an empty flag.
# Judgement-heavy types get the stronger model; derivation and build carry
# the narrowest context and run on the ambient default.
MODELS: dict[str, str | None] = {
    TRIAGE: "claude-opus-5",
    BACK_CHECK: "claude-opus-5",
    SLICING: "claude-opus-5",
}

TYPE_FILE = "CLAUDE.md"


@dataclasses.dataclass(frozen=True)
class SessionResult:
    session_type: str
    project: str
    ok: bool
    duration_seconds: float
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "session_type": self.session_type,
            "project": self.project,
            "ok": self.ok,
            "duration_seconds": self.duration_seconds,
            "detail": self.detail,
        }


def discover(root: Path) -> set[str]:
    """Every valid session type under `root`. A directory without a
    `CLAUDE.md` is not one -- it has no prompt, so there is nothing to run."""
    root = Path(root)
    if not root.is_dir():
        return set()
    return {d.name for d in root.iterdir() if (d / TYPE_FILE).is_file()}


def argv_for(prompt: str, model: str | None) -> list[str]:
    argv = ["claude", "-p", prompt]
    if model:
        argv += ["--model", model]
    return argv


def _spawn(argv: Sequence[str], cwd: Path) -> tuple[int, str]:
    done = subprocess.run(
        list(argv), cwd=str(cwd), capture_output=True, text=True
    )
    return done.returncode, (done.stderr or done.stdout or "").strip()


def run(
    dispatch: Dispatch,
    prompt: str,
    *,
    root: Path,
    spawn: Callable[[Sequence[str], Path], tuple[int, str]] = _spawn,
    now_fn: Callable[[], float] = time.monotonic,
    models: dict[str, str | None] | None = None,
) -> SessionResult:
    """Run one session to completion. Bounded work then a clean exit is
    success; a non-zero exit is a failed result, never an exception."""
    cwd = Path(root) / dispatch.session_type
    if not (cwd / TYPE_FILE).is_file():
        return SessionResult(
            dispatch.session_type,
            dispatch.project,
            False,
            0.0,
            f"no session type at {cwd}",
        )

    model = (MODELS if models is None else models).get(dispatch.session_type)
    started = now_fn()
    code, detail = spawn(argv_for(prompt, model), cwd)
    return SessionResult(
        dispatch.session_type,
        dispatch.project,
        code == 0,
        now_fn() - started,
        detail,
    )


@contextlib.contextmanager
def gate(path: Path) -> Iterator[bool]:
    """One loop at a time. Yields False when another holds the lock, and in
    that case does not touch it -- a loser that cleans up on its way out
    would delete the winner's lock, which is the whole bug this guards."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        yield False
        return
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield True
    finally:
        path.unlink(missing_ok=True)
