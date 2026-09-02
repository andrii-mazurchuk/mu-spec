from __future__ import annotations

import json
from pathlib import Path

from mu_spec.server import UNIT_NAME, handle, read_prompt


def _prompts(tmp_path: Path, **files: str) -> Path:
    d = tmp_path / "prompts"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        (d / f"{name}.md").write_text(body, encoding="utf-8")
    return d


# -- the four standard endpoints --------------------------------------------


def test_health_returns_ok(tmp_path):
    status, content_type, body = handle("GET", "/health", _prompts(tmp_path))
    assert status == 200
    assert content_type == "application/json"
    assert json.loads(body) == {"status": "ok"}


def test_stats_envelope_is_the_standard_shape(tmp_path):
    """The envelope is identical across every unit in the system; only
    `metrics` is unit-specific. Clock and metrics are injected so this
    asserts on a real value rather than "some float"."""
    status, _, body = handle(
        "GET",
        "/stats",
        _prompts(tmp_path),
        now_fn=lambda: 1234.5,
        metrics_fn=lambda: {"documents": 7},
    )
    assert status == 200
    assert json.loads(body) == {
        "unit": UNIT_NAME,
        "computed_at": 1234.5,
        "metrics": {"documents": 7},
    }


def test_tools_manifest_is_served_even_while_empty(tmp_path):
    """The bridge fetches /tools from every peer. A unit with no tools yet
    must still answer with the right shape -- a missing or malformed
    response drops this unit out of discovery entirely."""
    status, _, body = handle("GET", "/tools", _prompts(tmp_path))
    assert status == 200
    assert json.loads(body) == {"unit": UNIT_NAME, "tools": []}


def test_prompts_default_tier_returns_raw_text(tmp_path):
    d = _prompts(tmp_path, default="# mu-spec\n\nwhat this unit is.\n")
    status, content_type, body = handle("GET", "/prompts/default", d)
    assert status == 200
    assert content_type.startswith("text/plain")
    assert body == "# mu-spec\n\nwhat this unit is.\n"


# -- degradation: absence is normal, never an exception ---------------------


def test_missing_prompt_file_404s_rather_than_raising(tmp_path):
    status, _, body = handle("GET", "/prompts/reference", _prompts(tmp_path))
    assert status == 404
    assert "reference" in json.loads(body)["error"]


def test_unknown_prompt_tier_is_rejected_without_touching_the_filesystem(tmp_path):
    """Tiers are a closed set. An arbitrary tier must not become an
    arbitrary file read -- `/prompts/../../secrets` resolves to nothing."""
    assert read_prompt(_prompts(tmp_path), "../../etc/passwd") is None
    assert read_prompt(_prompts(tmp_path), "made-up") is None


def test_unknown_path_404s(tmp_path):
    status, _, _ = handle("GET", "/nope", _prompts(tmp_path))
    assert status == 404


def test_non_get_is_rejected(tmp_path):
    status, _, _ = handle("POST", "/health", _prompts(tmp_path))
    assert status == 405


def test_the_shipped_default_prompt_is_actually_servable():
    """prompts/default.md is declared in this unit's manifest entry and
    fetched by peers assembling their own context. If it goes missing or
    gets renamed, peers silently learn nothing about mu-spec."""
    status, _, body = handle("GET", "/prompts/default", Path("prompts"))
    assert status == 200
    assert body.strip()
