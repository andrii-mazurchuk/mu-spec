from __future__ import annotations

from pathlib import Path

from mu_spec.dispatch import DERIVATION, SESSION_TYPES, Dispatch
from mu_spec.runner import argv_for, discover, gate, resolve, run


def _types(tmp_path, *names):
    for name in names:
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / "CLAUDE.md").write_text(f"# {name} session\n", encoding="utf-8")
    return tmp_path


def _dispatch(kind=DERIVATION):
    return Dispatch(kind, "m", {"layer": "A", "slice": "listings"}, "because")


class _Spawn:
    """Stands in for the real `claude` process. The runner is injectable
    precisely so the suite never spends an API call."""

    def __init__(self, code=0, detail=""):
        self.code, self.detail, self.calls = code, detail, []

    def __call__(self, argv, cwd, prompt=""):
        self.calls.append((list(argv), cwd, prompt))
        return self.code, self.detail


# -- discovery ---------------------------------------------------------------


def test_a_session_type_is_a_directory_with_a_claude_md(tmp_path):
    """Derived from the filesystem, so there is no manifest to go stale."""
    _types(tmp_path, "derivation", "triage")
    (tmp_path / "notes").mkdir()  # no CLAUDE.md -- not a session type
    assert discover(tmp_path) == {"derivation", "triage"}


def test_discovery_of_a_missing_root_is_empty_not_an_error(tmp_path):
    assert discover(tmp_path / "nope") == set()


# -- the command -------------------------------------------------------------


def test_the_model_flag_is_passed_when_a_type_declares_one():
    assert "--model" in argv_for("claude-opus-5")
    assert "claude-opus-5" in argv_for("claude-opus-5")


def test_the_model_flag_is_omitted_entirely_when_none():
    """Omitted, not passed empty -- that leaves the CLI's own default in
    effect rather than overriding it with nothing."""
    assert "--model" not in argv_for(None)


def test_the_prompt_is_never_an_argument():
    """It goes over stdin. As an argv element it is truncated at the first
    newline by the Windows .CMD shim, which is what silently reduced the
    first three live briefs to their heading."""
    argv = argv_for(None)
    assert "the whole brief" not in " ".join(argv)
    assert argv[:2] == ["claude", "-p"]


def test_the_prompt_reaches_the_launcher_over_stdin(tmp_path):
    _types(tmp_path, DERIVATION)
    spawn = _Spawn()
    run(_dispatch(), "the whole brief\nwith a second line", root=tmp_path, spawn=spawn)
    _argv, _cwd, prompt = spawn.calls[0]
    assert prompt == "the whole brief\nwith a second line"


def test_a_multi_line_prompt_survives_the_real_launcher(tmp_path):
    """The regression that cost three live runs, pinned against the real
    subprocess path rather than a fake."""
    import sys

    from mu_spec.runner import _spawn

    code, detail = _spawn(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        tmp_path,
        "line one\nline two",
    )
    assert code == 0
    assert detail == "line one\nline two"


# -- running -----------------------------------------------------------------


def test_a_clean_exit_is_success(tmp_path):
    _types(tmp_path, DERIVATION)
    spawn = _Spawn(code=0)
    result = run(_dispatch(), "prompt", root=tmp_path, spawn=spawn)
    assert result.ok is True
    assert result.session_type == DERIVATION
    assert result.project == "m"


def test_the_session_runs_in_its_own_type_directory(tmp_path):
    """cwd is the whole mechanism: Claude Code loads that directory's
    CLAUDE.md natively, so nothing here assembles the static prompt."""
    _types(tmp_path, DERIVATION)
    spawn = _Spawn()
    run(_dispatch(), "prompt", root=tmp_path, spawn=spawn)
    _, cwd, _prompt = spawn.calls[0]
    assert cwd == tmp_path / DERIVATION


def test_a_nonzero_exit_is_a_failed_result_not_an_exception(tmp_path):
    _types(tmp_path, DERIVATION)
    result = run(
        _dispatch(), "prompt", root=tmp_path, spawn=_Spawn(code=2, detail="boom")
    )
    assert result.ok is False
    assert "boom" in result.detail


def test_an_unknown_session_type_never_launches_anything(tmp_path):
    """A missing directory is a deployment fault. It degrades to a failed
    result rather than raising into a request handler -- but it must not
    launch a session with the wrong cwd."""
    _types(tmp_path, DERIVATION)
    spawn = _Spawn()
    result = run(_dispatch("triage"), "prompt", root=tmp_path, spawn=spawn)
    assert result.ok is False
    assert spawn.calls == []


def test_the_result_carries_a_duration(tmp_path):
    """mu-logs rolls up avg_duration_seconds from session_run entries, so
    this is not decoration."""
    _types(tmp_path, DERIVATION)
    clock = iter([10.0, 12.5])
    result = run(
        _dispatch(), "prompt", root=tmp_path, spawn=_Spawn(), now_fn=lambda: next(clock)
    )
    assert result.duration_seconds == 2.5


# -- the gate ----------------------------------------------------------------


def test_the_gate_admits_one_holder(tmp_path):
    lock = tmp_path / "loop.lock"
    with gate(lock) as first:
        assert first is True
        with gate(lock) as second:
            assert second is False


def test_the_gate_releases_on_the_way_out(tmp_path):
    lock = tmp_path / "loop.lock"
    with gate(lock) as held:
        assert held is True
    assert not lock.exists()
    with gate(lock) as again:
        assert again is True


def test_a_refused_gate_does_not_release_the_holders_lock(tmp_path):
    """The classic bug: the loser's cleanup deletes the winner's lock."""
    lock = tmp_path / "loop.lock"
    with gate(lock):
        with gate(lock) as second:
            assert second is False
        assert lock.exists()


def test_the_gate_releases_even_when_the_body_raises(tmp_path):
    lock = tmp_path / "loop.lock"
    try:
        with gate(lock):
            raise RuntimeError("session blew up")
    except RuntimeError:
        pass
    assert not lock.exists()


# -- the prompts that actually ship ------------------------------------------

_REPO_TYPES = Path(__file__).resolve().parent.parent / "session_types"


def test_every_ladder_rung_has_a_prompt_on_disk():
    """The ladder dispatches by name and the runner resolves that name to a
    directory. A rename on one side and not the other would dispatch a
    session type that cannot be launched."""
    assert discover(_REPO_TYPES) == set(SESSION_TYPES)


def test_every_prompt_points_at_the_shared_contract():
    """SHARED.md carries the issue obligation and the storage boundary. A
    prompt that never tells the session to read it drops both."""
    for name in SESSION_TYPES:
        text = (_REPO_TYPES / name / "CLAUDE.md").read_text(encoding="utf-8")
        assert "../SHARED.md" in text, name


def test_the_shared_contract_is_not_itself_a_session_type():
    assert "SHARED" not in discover(_REPO_TYPES)


def test_a_session_that_overruns_is_killed_and_reported(tmp_path):
    """No timeout means one hung session holds the lock forever and every
    later trigger reports the lock instead of doing work."""
    import subprocess

    _types(tmp_path, DERIVATION)

    def hang(argv, cwd, prompt=""):
        raise subprocess.TimeoutExpired(argv, 0.01)

    result = run(_dispatch(), "prompt", root=tmp_path, spawn=hang)
    assert result.ok is False
    assert "timed out" in result.detail.lower()


def test_the_launcher_name_is_resolved_before_it_is_executed():
    """On Windows `claude` is a .CMD shim, and CreateProcess does not apply
    PATHEXT to a bare name in list form -- so the unresolved name raises
    FileNotFoundError and no session ever starts."""
    import shutil

    resolved = resolve("claude")
    assert resolved == (shutil.which("claude") or "claude")


def test_an_unresolvable_name_is_returned_unchanged():
    assert resolve("definitely-not-a-real-binary") == "definitely-not-a-real-binary"


def test_a_launcher_that_cannot_be_started_is_a_failed_result(tmp_path):
    """The first live run died here: the exception escaped run_pipeline and
    reached the request handler, which returned an empty body. Nothing may
    escape into a handler -- a session that cannot start is a failed result."""
    _types(tmp_path, DERIVATION)

    def missing(argv, cwd, prompt=""):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    result = run(_dispatch(), "prompt", root=tmp_path, spawn=missing)
    assert result.ok is False
    assert "could not start" in result.detail.lower()


def test_stdout_is_the_record_not_stderr(tmp_path):
    """The first live run reported three unrelated permission warnings as
    the session's result. `claude -p` writes its answer to stdout and this
    environment always has something on stderr, so `stderr or stdout`
    discarded the answer every single time."""
    import sys

    from mu_spec.runner import _spawn

    code, detail = _spawn(
        [sys.executable, "-c",
         "import sys; sys.stdin.read(); sys.stdout.write('THE ANSWER');"
         " sys.stderr.write('noise')"],
        tmp_path,
    )
    assert code == 0
    assert detail == "THE ANSWER"


def test_a_failure_keeps_stderr_because_that_is_where_the_reason_is(tmp_path):
    import sys

    from mu_spec.runner import _spawn

    code, detail = _spawn(
        [sys.executable, "-c",
         "import sys; sys.stdin.read();"
         " sys.stderr.write('what went wrong'); sys.exit(3)"],
        tmp_path,
    )
    assert code == 3
    assert "what went wrong" in detail


def test_a_session_is_granted_the_tools_it_needs():
    """The first session that actually read its brief could do nothing: every
    curl, python and PowerShell call came back "This command requires
    approval", and a headless run has nobody to approve it."""
    argv = argv_for(None)
    joined = " ".join(argv)
    assert "--allowedTools" in argv
    assert "curl" in joined


def test_only_build_may_write_files():
    """Every other session type writes entries through the API. A derivation
    session holding Write could edit the graph on disk and skip every gate."""
    from mu_spec.dispatch import BUILD

    assert "Write" not in " ".join(argv_for(None, DERIVATION))
    assert "Write" in " ".join(argv_for(None, BUILD))
