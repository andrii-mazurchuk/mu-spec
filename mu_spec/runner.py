"""Launching one session, and the lock that stops two loops overlapping.

A session type is a directory containing a `CLAUDE.md`. Claude Code loads
that file natively once the process starts with the directory as `cwd`, so
nothing here assembles the static half of a prompt -- only the dynamic,
per-run half, and that goes over stdin rather than argv. Discovery reads the filesystem rather than a
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
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator, Sequence

from .dispatch import BACK_CHECK, BUILD, DERIVATION, Dispatch, SLICING, TRIAGE

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

# What a session is permitted to do. A headless run cannot answer a
# permission prompt -- the first session that actually read its brief tried
# curl, python and PowerShell in turn and got "This command requires
# approval" for each, then correctly refused to read state/ directly and
# reported that it could do nothing.
#
# Scoped rather than bypassed. curl is the sanctioned HTTP path because a
# narrow allowlist is predictable; Write and Edit are withheld from every
# type but build, since everything else writes through the API where the
# admission gates are.
BASE_TOOLS = ("Bash(curl *)", "Read", "Grep", "Glob")
BUILD_TOOLS = BASE_TOOLS + ("Write", "Edit", "Bash(git *)")

# How long one session may run before it is killed. Not a cost control --
# it is what stops a hung session holding the loop's lock forever, which
# would make every later trigger report the lock instead of doing work.
DEFAULT_TIMEOUT = float(os.environ.get("MU_SPEC_SESSION_TIMEOUT", 1800))


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


def tools_for(session_type: str | None) -> tuple[str, ...]:
    return BUILD_TOOLS if session_type == BUILD else BASE_TOOLS


def argv_for(model: str | None, session_type: str | None = None) -> list[str]:
    """The command, without the prompt.

    The prompt is deliberately absent: it goes over stdin. On Windows the CLI
    resolves to a `.CMD` shim, which runs through `cmd.exe`, and a multi-line
    argument is truncated at the first newline there -- the first three live
    sessions each received only the brief's `# Session brief` heading and
    replied asking what was wanted. stdin has no such limit.
    """
    argv = ["claude", "-p", "--allowedTools", *tools_for(session_type)]
    if model:
        argv += ["--model", model]
    return argv


def resolve(name: str) -> str:
    """Turn a launcher name into something CreateProcess can actually start.

    On Windows the CLI is installed as `claude.CMD`, and passing the bare
    name in list form does not apply PATHEXT -- it raises FileNotFoundError
    and no session ever starts. `which` does apply it. Unresolvable names are
    returned unchanged so the caller sees the real OS error rather than a
    substituted one.
    """
    return shutil.which(name) or name


def _spawn(
    argv: Sequence[str],
    cwd: Path,
    prompt: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, str]:
    launcher, *rest = argv
    done = subprocess.run(
        [resolve(launcher), *rest],
        cwd=str(cwd),
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    # stdout is where `claude -p` writes its answer. stderr routinely carries
    # unrelated warnings, so preferring it discarded the session's actual
    # output on every run -- it is kept only when something failed, which is
    # the one case it explains anything.
    detail = (done.stdout or "").strip()
    if done.returncode != 0:
        parts = [p for p in (detail, (done.stderr or "").strip()) if p]
        detail = chr(10).join(parts)
    return done.returncode, detail


def run(
    dispatch: Dispatch,
    prompt: str,
    *,
    root: Path,
    spawn: Callable[[Sequence[str], Path, str], tuple[int, str]] = _spawn,
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
    try:
        code, detail = spawn(
            argv_for(model, dispatch.session_type), cwd, prompt
        )
    except subprocess.TimeoutExpired:
        # A killed session is a failed result like any other. Raising here
        # would take the loop down with it and leave the lock behind.
        return SessionResult(
            dispatch.session_type,
            dispatch.project,
            False,
            now_fn() - started,
            "timed out and was killed",
        )
    except OSError as exc:
        # The launcher is missing, unresolvable, or not executable. This is
        # the outermost degradation boundary for starting a process: nothing
        # from here may escape into the request handler that triggered it.
        return SessionResult(
            dispatch.session_type,
            dispatch.project,
            False,
            now_fn() - started,
            f"could not start the session: {exc}",
        )
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
