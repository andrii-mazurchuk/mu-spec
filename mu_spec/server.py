"""The HTTP surface: the standard unit contract plus this unit's own
operations.

Routing is split from transport on purpose. `handle()` takes a method, a
path, a parsed body and its dependencies, and returns a (status,
content_type, body) triple -- it opens no socket. The
BaseHTTPRequestHandler at the bottom is the only code here that knows HTTP
exists, so every route is testable without a server running.

The tool manifest is hand-written rather than derived from the route table:
each entry needs a description written for a model to read, and the two are
deliberately allowed to differ -- /health and /tools are never declared.
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from mu_spec import service
from mu_spec.service import ServiceError
from mu_spec.storage import MalformedEntryFile, ProjectStore, UnknownProject

UNIT_NAME = "mu-spec"
PROMPT_TIERS = ("default", "reference")

JSON = "application/json"
TEXT = "text/plain; charset=utf-8"

_ID = r"[A-Z]·[0-9]+"
_P = r"(?P<project>[A-Za-z0-9_-]+)"


def _tools() -> list[dict[str, Any]]:
    """What a model sees. The six operations a caller actually wants, plus
    the machinery those need: a write path for propagation, and the
    retrieval primitives that make loading context cheap."""

    def tool(name, desc, method, path, props, required=()):
        return {
            "name": name,
            "description": desc,
            "method": method,
            "path": path,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": list(required),
            },
        }

    s = {"type": "string"}
    return [
        tool(
            "initiate_project",
            "Start a project by stating intent -- the buyer's problem in their "
            "own terms. This is where requirements end from the human side; "
            "every lower layer is derived, never typed in here.",
            "POST",
            "/projects",
            {"project": s, "intent": {"type": "array", "items": {"type": "object"}}},
            ("project", "intent"),
        ),
        tool(
            "add_feature",
            "Request a new feature. Enters as an intent amendment -- a new "
            "numbered entry, never an edit to existing text. Returns the new "
            "identifier; propagating it downward is a separate step.",
            "POST",
            "/projects/{project}/features",
            {"project": s, "title": s, "body": s},
            ("project", "title"),
        ),
        tool(
            "report_defect",
            "Fix something that is wrong. You must classify which layer the "
            "error actually lives in (I intent, B behaviour, A architecture, "
            "S spec) -- patching the symptom where it was noticed leaves the "
            "layers above still lying. Returns the blast radius that must now "
            "be re-derived.",
            "POST",
            "/projects/{project}/defects",
            {
                "project": s,
                "layer": s,
                "title": s,
                "body": s,
                "supersedes": s,
                "slice": s,
                "derives_from": {"type": "array", "items": s},
            },
            ("project", "layer", "title"),
        ),
        tool(
            "comment_on_entry",
            "Attach a question or observation to an entry. A comment is an "
            "annotation, never an amendment: it does not enter the graph and "
            "can never silently become a decision.",
            "POST",
            "/projects/{project}/comments",
            {"project": s, "target": s, "body": s, "author": s},
            ("project", "target", "body"),
        ),
        tool(
            "get_work_package",
            "Retrieve the bounded context for producing code for one slice: "
            "the spec entries you may edit, the justification chain for each, "
            "read-only context from declared dependencies, and cross-cutting "
            "entries. Refused if the graph does not pass its admission gates.",
            "GET",
            "/projects/{project}/work-package",
            {"project": s, "slice": s},
            ("project", "slice"),
        ),
        tool(
            "review_layer",
            "Read one layer with each entry's justification chain, what "
            "serves it, and its comments attached -- so a reviewer sees the "
            "decision and what it claims to serve in one place.",
            "GET",
            "/projects/{project}/review",
            {"project": s, "layer": s, "slice": s},
            ("project", "layer"),
        ),
        tool(
            "submit_amendment",
            "Record a batch of derived entries -- the result of propagating a "
            "change into a lower layer. Validated as one transaction: an "
            "amendment that would introduce an orphan is refused whole. "
            "Entries left unserved are reported, not refused.",
            "POST",
            "/projects/{project}/amendments",
            {
                "project": s,
                "slice": s,
                "entries": {"type": "array", "items": {"type": "object"}},
            },
            ("project", "entries"),
        ),
        tool(
            "get_spine",
            "Every entry's identifier, one-line title and derives-from, with "
            "no bodies. Load this first, decide from the structure what you "
            "need, then fetch those bodies by identifier.",
            "GET",
            "/projects/{project}/spine",
            {"project": s, "layer": s},
            ("project",),
        ),
        tool(
            "get_entry",
            "One entry's full body, its parents, and what derives from it.",
            "GET",
            "/projects/{project}/entries/{id}",
            {"project": s, "id": s},
            ("project", "id"),
        ),
        tool(
            "check_gates",
            "Run the mechanical admission gates: entries tracing to nothing "
            "above (orphans), and entries nothing below serves (unserved). "
            "An empty findings list means the graph holds together.",
            "GET",
            "/projects/{project}/gates",
            {"project": s},
            ("project",),
        ),
        tool(
            "list_projects",
            "Every project this unit holds.",
            "GET",
            "/projects",
            {},
        ),
    ]


def read_prompt(prompts_dir: Path, tier: str) -> str | None:
    """A missing prompt file is a normal outcome, not an error. The tier is
    checked against a closed set *before* a path is built, so an arbitrary
    tier can never become an arbitrary file read."""
    if tier not in PROMPT_TIERS:
        return None
    try:
        return (prompts_dir / f"{tier}.md").read_text(encoding="utf-8")
    except OSError:
        return None


_ROUTES: list[tuple[str, "re.Pattern[str]", str]] = [
    ("GET", re.compile(r"^/health$"), "health"),
    ("GET", re.compile(r"^/stats$"), "stats"),
    ("GET", re.compile(r"^/tools$"), "tools"),
    ("GET", re.compile(r"^/prompts/(?P<tier>[^/]+)$"), "prompts"),
    ("GET", re.compile(r"^/projects$"), "list_projects"),
    ("POST", re.compile(r"^/projects$"), "initiate"),
    ("GET", re.compile(rf"^/projects/{_P}/spine$"), "spine"),
    ("GET", re.compile(rf"^/projects/{_P}/gates$"), "gates"),
    ("GET", re.compile(rf"^/projects/{_P}/entries/(?P<id>{_ID})$"), "entry"),
    ("POST", re.compile(rf"^/projects/{_P}/features$"), "feature"),
    ("POST", re.compile(rf"^/projects/{_P}/defects$"), "defect"),
    ("POST", re.compile(rf"^/projects/{_P}/comments$"), "comment"),
    ("GET", re.compile(rf"^/projects/{_P}/comments$"), "list_comments"),
    ("POST", re.compile(rf"^/projects/{_P}/amendments$"), "amendment"),
    ("GET", re.compile(rf"^/projects/{_P}/work-package$"), "work_package"),
    ("GET", re.compile(rf"^/projects/{_P}/review$"), "review"),
]


def handle(
    method: str,
    raw_path: str,
    store: ProjectStore,
    prompts_dir: Path,
    body: dict | None = None,
    now_fn: Callable[[], float] = time.time,
) -> tuple[int, str, str]:
    """Resolve one request to (status, content_type, body)."""
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    body = body or {}

    name: str | None = None
    params: dict[str, str] = {}
    path_exists = False
    for route_method, pattern, route_name in _ROUTES:
        match = pattern.match(path)
        if match:
            path_exists = True
            if route_method == method:
                name, params = route_name, match.groupdict()
                break

    if name is None:
        if path_exists:
            return 405, JSON, json.dumps({"error": "method not allowed"})
        return 404, JSON, json.dumps({"error": "not found"})

    project = params.get("project", "")
    try:
        if name == "health":
            return 200, JSON, json.dumps({"status": "ok"})

        if name == "stats":
            projects = store.list_projects()
            entries = sum(len(store.load_all(p)) for p in projects)
            return (
                200,
                JSON,
                json.dumps(
                    {
                        "unit": UNIT_NAME,
                        "computed_at": now_fn(),
                        "metrics": {"projects": len(projects), "entries": entries},
                    }
                ),
            )

        if name == "tools":
            return 200, JSON, json.dumps({"unit": UNIT_NAME, "tools": _tools()})

        if name == "prompts":
            text = read_prompt(prompts_dir, params["tier"])
            if text is None:
                return (
                    404,
                    JSON,
                    json.dumps({"error": f"no prompt for tier {params['tier']!r}"}),
                )
            return 200, TEXT, text

        if name == "list_projects":
            return 200, JSON, json.dumps({"projects": store.list_projects()})

        if name == "initiate":
            return 201, JSON, json.dumps(service.initiate_project(store, body))

        if name == "spine":
            return (
                200,
                JSON,
                json.dumps(service.get_spine(store, project, query.get("layer"))),
            )

        if name == "gates":
            return 200, JSON, json.dumps(service.check_gates(store, project))

        if name == "entry":
            return (
                200,
                JSON,
                json.dumps(service.get_entry(store, project, params["id"])),
            )

        if name == "feature":
            return 201, JSON, json.dumps(service.add_feature(store, project, body))

        if name == "defect":
            return 201, JSON, json.dumps(service.report_defect(store, project, body))

        if name == "comment":
            return (
                201,
                JSON,
                json.dumps(service.comment_on_entry(store, project, body, now_fn)),
            )

        if name == "list_comments":
            return (
                200,
                JSON,
                json.dumps(service.list_comments(store, project, query.get("target"))),
            )

        if name == "amendment":
            result = service.submit_amendment(store, project, body)
            return (200 if result["admitted"] else 409), JSON, json.dumps(result)

        if name == "work_package":
            if not query.get("slice"):
                return 400, JSON, json.dumps({"error": "'slice' is required"})
            result = service.get_work_package(store, project, query["slice"])
            return (200 if result["issued"] else 409), JSON, json.dumps(result)

        if name == "review":
            if not query.get("layer"):
                return 400, JSON, json.dumps({"error": "'layer' is required"})
            return (
                200,
                JSON,
                json.dumps(
                    service.review_layer(
                        store, project, query["layer"], query.get("slice")
                    )
                ),
            )

    except UnknownProject:
        return 404, JSON, json.dumps({"error": f"unknown project {project!r}"})
    except FileExistsError as exc:
        return 409, JSON, json.dumps({"error": str(exc)})
    except (ServiceError, MalformedEntryFile, ValueError) as exc:
        # A caller error, reported as one. Distinct from the degrade-quietly
        # rule that governs optional inputs: a malformed write must never be
        # half-accepted, because a half-accepted write is how the graph rots.
        return 400, JSON, json.dumps({"error": str(exc)})

    return 500, JSON, json.dumps({"error": "unrouted"})


def build_handler(
    store: ProjectStore, prompts_dir: Path
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            payload = None
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._write(400, JSON, json.dumps({"error": "invalid JSON"}))
                    return
                if not isinstance(payload, dict):
                    self._write(
                        400, JSON, json.dumps({"error": "body must be an object"})
                    )
                    return
            status, content_type, body = handle(
                method, self.path, store, prompts_dir, payload
            )
            self._write(status, content_type, body)

        def _write(self, status: int, content_type: str, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 -- stdlib's naming
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def log_message(self, *_args: Any) -> None:
            """Silence stdlib's per-request stderr logging: the gateway polls
            /health on an interval and would otherwise drown anything real."""

    return _Handler


def serve(host: str, port: int, store: ProjectStore, prompts_dir: Path) -> None:
    ThreadingHTTPServer((host, port), build_handler(store, prompts_dir)).serve_forever()
