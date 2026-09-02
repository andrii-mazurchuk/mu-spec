"""The standard unit HTTP contract for mu-docs.

Every unit in this system implements /health, /stats, /tools and
/prompts/<tier> independently -- there is no shared base class, and that
duplication is deliberate. See CLAUDE.md.

Routing is split into a pure `handle()` and a thin BaseHTTPRequestHandler
wrapper on purpose: `handle()` takes its clock and its prompt-reader as
arguments and returns a (status, content_type, body) triple, so the whole
contract is testable without opening a socket or sleeping. The wrapper
below is the only part that knows about HTTP transport at all.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

UNIT_NAME = "mu-docs"

PROMPT_TIERS = ("default", "reference")

JSON = "application/json"
TEXT = "text/plain; charset=utf-8"

# Hand-written, not derived from the routing table below: each entry needs a
# description written for a model to read, and the two are allowed to differ
# (/health and /tools are deliberately never declared here). Grows as this
# unit gains real capabilities.
TOOLS: list[dict[str, Any]] = []


def _stats(now_fn: Callable[[], float], metrics: dict[str, Any]) -> dict[str, Any]:
    """The envelope is identical in every unit; only `metrics` differs, and
    it carries mechanical data only -- counts, sizes, timings -- never
    anything an agent judged."""
    return {
        "unit": UNIT_NAME,
        "computed_at": now_fn(),
        "metrics": metrics,
    }


def read_prompt(prompts_dir: Path, tier: str) -> str | None:
    """A missing prompt file is a normal outcome, not an error -- absence
    degrades everywhere in this system. Returns None so the caller can 404
    rather than raising out of a request handler."""
    if tier not in PROMPT_TIERS:
        return None
    path = prompts_dir / f"{tier}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def handle(
    method: str,
    path: str,
    prompts_dir: Path,
    now_fn: Callable[[], float] = time.time,
    metrics_fn: Callable[[], dict[str, Any]] = dict,
) -> tuple[int, str, str]:
    """Resolve one request to (status, content_type, body). Pure with
    respect to its injected clock and metrics source."""
    if method != "GET":
        return 405, JSON, json.dumps({"error": "method not allowed"})

    if path == "/health":
        return 200, JSON, json.dumps({"status": "ok"})

    if path == "/stats":
        return 200, JSON, json.dumps(_stats(now_fn, metrics_fn()))

    if path == "/tools":
        return 200, JSON, json.dumps({"unit": UNIT_NAME, "tools": TOOLS})

    if path.startswith("/prompts/"):
        tier = path[len("/prompts/") :]
        body = read_prompt(prompts_dir, tier)
        if body is None:
            return 404, JSON, json.dumps({"error": f"no prompt for tier {tier!r}"})
        return 200, TEXT, body

    return 404, JSON, json.dumps({"error": "not found"})


def build_handler(prompts_dir: Path) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- stdlib's naming, not ours
            status, content_type, body = handle("GET", self.path, prompts_dir)
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args: Any) -> None:
            """Silence stdlib's per-request stderr logging: the gateway polls
            /health on an interval, which would otherwise fill the log with
            one line every few seconds and drown anything real."""

    return _Handler


def serve(host: str, port: int, prompts_dir: Path) -> None:
    ThreadingHTTPServer((host, port), build_handler(prompts_dir)).serve_forever()
