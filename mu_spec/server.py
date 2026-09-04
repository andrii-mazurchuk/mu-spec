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
from mu_spec.inbox import Inbox, InboxError
from mu_spec.storage import MalformedEntryFile, ProjectStore, UnknownProject

UNIT_NAME = "mu-spec"
PROMPT_TIERS = ("default", "reference")

JSON = "application/json"
TEXT = "text/plain; charset=utf-8"

_ID = r"[A-Z]·[0-9]+"
_P = r"(?P<project>[A-Za-z0-9_-]+)"


def _tools() -> list[dict[str, Any]]:
    """What a model sees. Hand-written, not derived from the route table --
    every entry needs a description written for a model to read, and /health
    and /tools are deliberately never declared.

    Note what is NOT here: any way to write an entry directly. Everything
    from outside goes through post_request, and the request's *type* is what
    decides how deep a change may reach.
    """

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
    strings = {"type": "array", "items": s}
    return [
        tool(
            "post_request",
            "The only way to ask this unit for a change. `type` is one of: "
            "initiate (start a project from a raw idea), feature (something "
            "the product does not do yet), correction (something is wrong), "
            "comment (an observation attached to part of the design), "
            "question (needs an answer, not a change). You never name a layer "
            "or write an entry -- the type decides how deep the change may "
            "reach, and an agent decides what actually changes. `targets` is "
            "optional and usually omitted: you are not expected to know how "
            "the design is laid out.",
            "POST",
            "/inbox",
            {
                "type": s,
                "project": s,
                "title": s,
                "body": s,
                "targets": strings,
                "origin": s,
            },
            ("type", "title"),
        ),
        tool(
            "list_requests",
            "The request queue. Filter by status (pending/accepted/rejected), "
            "project, type, or target. This is what the processing agent "
            "reads to find work.",
            "GET",
            "/inbox",
            {"status": s, "project": s, "type": s, "target": s},
        ),
        tool(
            "get_request",
            "One request, with what it has produced so far.",
            "GET",
            "/inbox/{mid}",
            {"mid": s},
            ("mid",),
        ),
        tool(
            "resolve_request",
            "Close a request: accepted (with what it produced) or rejected "
            "(with why). Leaving it pending is what keeps the queue honest.",
            "POST",
            "/inbox/{mid}/resolve",
            {"mid": s, "status": s, "note": s, "produced": strings},
            ("mid",),
        ),
        tool(
            "create_project",
            "Create an empty project. Requires an `initiate` request to cite. "
            "The intent entries themselves arrive as a normal amendment, so "
            "they are derived and interviewed for rather than lifted verbatim "
            "from whatever the requester typed.",
            "POST",
            "/projects",
            {"project": s, "in_response_to": s},
            ("project", "in_response_to"),
        ),
        tool(
            "submit_amendment",
            "Record a batch of derived entries -- the pipeline's own write "
            "path. Must cite the request it serves. Each entry needs a "
            "'layer' and a 'title', and may carry 'body', 'supersedes', and "
            "three kinds of edge: 'derives_from' (identifiers exactly one "
            "layer up -- what it serves), 'depends_on' (same layer -- what it "
            "needs, and what imposes order), and 'emits_into' (same layer, "
            "and only into a cross-cutting slice -- what it publishes, "
            "fire-and-forget, imposing no order). Reaching a cross-cutting "
            "slice with depends_on is refused: if you branch on what comes "
            "back, it is not cross-cutting. Validated as one transaction: an "
            "amendment introducing an orphan, a broken horizontal edge, a "
            "slice cycle, or an outbound edge from a cross-cutting slice is "
            "refused whole, and the first entry created for a request must "
            "sit within that request type's permitted origination depth. "
            "Entries left unserved are reported, not refused.",
            "POST",
            "/projects/{project}/amendments",
            {
                "project": s,
                "slice": s,
                "in_response_to": s,
                "entries": {"type": "array", "items": {"type": "object"}},
            },
            ("project", "in_response_to", "entries"),
        ),
        tool(
            "classify_slice",
            "Record whether a slice is ordinary or cross-cutting. 'type' is "
            "'slice' or 'cross_cutting'. A cross-cutting slice's spec spine "
            "lands in every other slice's work package without anything "
            "declaring a dependency on it, because its behaviour ranges over "
            "the other slices rather than naming a subject of its own. The "
            "unit records this ruling; it does not make it -- but it refuses "
            "one that contradicts edges that already exist, so a slice "
            "already reaching into a feature slice cannot be relabelled "
            "cross-cutting after the fact.",
            "POST",
            "/projects/{project}/slices/{slice}/type",
            {"project": s, "slice": s, "type": s},
            ("project", "slice", "type"),
        ),
        tool(
            "get_work_package",
            "The bounded context for producing code for one slice: the spec "
            "entries you may edit, the justification chain for each, "
            "read-only context from the slices this one depends on "
            "(projected from the entries' own edges, never declared beside "
            "them), and cross-cutting entries. Refused if the graph is "
            "unsound.",
            "GET",
            "/projects/{project}/work-package",
            {"project": s, "slice": s},
            ("project", "slice"),
        ),
        tool(
            "review_layer",
            "Read one layer with each entry's justification chain, what "
            "serves it, and the comments attached to it -- so a reviewer sees "
            "the decision and what it claims to serve in one place.",
            "GET",
            "/projects/{project}/review",
            {"project": s, "layer": s, "slice": s},
            ("project", "layer"),
        ),
        tool(
            "get_spine",
            "Every entry's identifier, one-line title and all three edge "
            "lists, with "
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
            "Soundness and completeness. Sound means no orphans -- nothing "
            "derives from something missing, retired, or below it. Complete "
            "means nothing is unserved -- knowledge has reached spec on every "
            "branch. Unsound blocks work; incomplete is the to-do list.",
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
    # The single external door.
    ("POST", re.compile(r"^/inbox$"), "post_inbox"),
    ("GET", re.compile(r"^/inbox$"), "list_inbox"),
    ("GET", re.compile(r"^/inbox/(?P<mid>[A-Za-z0-9_-]+)$"), "get_message"),
    ("POST", re.compile(r"^/inbox/(?P<mid>[A-Za-z0-9_-]+)/resolve$"), "resolve"),
    # The pipeline's own write path.
    ("POST", re.compile(r"^/projects$"), "create_project"),
    ("POST", re.compile(rf"^/projects/{_P}/amendments$"), "amendment"),
    (
        "POST",
        re.compile(rf"^/projects/{_P}/slices/(?P<slice>[A-Za-z0-9_-]+)/type$"),
        "classify_slice",
    ),
    # Reads.
    ("GET", re.compile(r"^/projects$"), "list_projects"),
    ("GET", re.compile(rf"^/projects/{_P}/spine$"), "spine"),
    ("GET", re.compile(rf"^/projects/{_P}/gates$"), "gates"),
    ("GET", re.compile(rf"^/projects/{_P}/entries/(?P<id>{_ID})$"), "entry"),
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
    inbox: Inbox | None = None,
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
    inbox = inbox or Inbox(store.inbox_path())
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

        if name == "post_inbox":
            return 201, JSON, json.dumps(service.post_to_inbox(inbox, body, now_fn))

        if name == "list_inbox":
            return 200, JSON, json.dumps(service.list_inbox(inbox, query))

        if name == "get_message":
            return 200, JSON, json.dumps(service.get_message(inbox, params["mid"]))

        if name == "resolve":
            return (
                200,
                JSON,
                json.dumps(service.resolve_message(inbox, params["mid"], body)),
            )

        if name == "create_project":
            return 201, JSON, json.dumps(service.create_project(store, inbox, body))

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

        if name == "amendment":
            result = service.submit_amendment(store, inbox, project, body)
            return (200 if result["admitted"] else 409), JSON, json.dumps(result)

        if name == "classify_slice":
            result = service.classify_slice(
                store, project, match.group("slice"), body or {}
            )
            return (200 if result["recorded"] else 409), JSON, json.dumps(result)

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
                        store, inbox, project, query["layer"], query.get("slice")
                    )
                ),
            )

    except InboxError as exc:
        return 400, JSON, json.dumps({"error": str(exc)})
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
