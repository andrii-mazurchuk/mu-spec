from __future__ import annotations

from pathlib import Path

from mu_spec.dispatch import DERIVATION, SESSION_TYPES, Dispatch
from mu_spec.runner import argv_for, discover, gate, run


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

    def __call__(self, argv, cwd):
        self.calls.append((list(argv), cwd))
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
    assert "--model" in argv_for("prompt", "claude-opus-5")
    assert "claude-opus-5" in argv_for("prompt", "claude-opus-5")


def test_the_model_flag_is_omitted_entirely_when_none():
    """Omitted, not passed empty -- that leaves the CLI's own default in
    effect rather than overriding it with nothing."""
    assert "--model" not in argv_for("prompt", None)


def test_the_prompt_is_passed_with_dash_p():
    argv = argv_for("do the thing", None)
    assert argv[argv.index("-p") + 1] == "do the thing"


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
    _, cwd = spawn.calls[0]
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

    def hang(argv, cwd):
        raise subprocess.TimeoutExpired(argv, 0.01)

    result = run(_dispatch(), "prompt", root=tmp_path, spawn=hang)
    assert result.ok is False
    assert "timed out" in result.detail.lower()
