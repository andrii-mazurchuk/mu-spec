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
from mu_spec.issues import IssueError, IssueLog
from mu_spec.lifecycle import Lifecycle
from mu_spec.shipping import ship
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
            "raise_issue",
            "File a request against another slice's entry, then proceed on "
            "your own stated assumption. There is no agent-to-agent channel "
            "and deliberately so: the agent that wrote the target is gone, "
            "and messaging a live one would mean blocking or nondeterminism. "
            "`kind` is 'additive' (a new entry is needed; nothing existing "
            "changes meaning, so nothing re-runs) or 'semantic' (an existing "
            "entry others already consumed now means something else; its "
            "consumers are invalidated). That call is a judgement about "
            "meaning, so you make it and this unit records it. Put the "
            "claim on one line -- the router reads headers only -- and put "
            "what you assumed in `assumption`.",
            "POST",
            "/projects/{project}/issues",
            {
                "project": s,
                "target": s,
                "kind": s,
                "claim": s,
                "assumption": s,
                "raised_by": s,
                "round": {"type": "integer"},
            },
            ("project", "target", "kind", "claim"),
        ),
        tool(
            "list_issues",
            "The issue queue for a project. Filter by status "
            "(open/resolved/escalated) or by the slice an issue targets.",
            "GET",
            "/projects/{project}/issues",
            {"project": s, "status": s, "slice": s},
            ("project",),
        ),
        tool(
            "close_issue",
            "Close an issue: resolved (with what it produced) or escalated "
            "(it needs a human).",
            "POST",
            "/projects/{project}/issues/{iid}/close",
            {"project": s, "iid": s, "status": s, "note": s, "produced": strings},
            ("project", "iid"),
        ),
        tool(
            "get_reconciliation",
            "The open issue queue grouped into one repair batch per target "
            "slice, in dependency order. Run this after every wave, not once "
            "per layer. Each batch carries its re-run scope: the entries that "
            "consumed a meaning one of its issues says has moved, computed at "
            "entry level, so usually a handful rather than a column. "
            "`escalations` are the issues that are not repairs -- past the "
            "two-round cap, or semantically reaching back into a wave that is "
            "already finished -- and those need a human. This returns the "
            "batches as data; running a repair session is the caller's job.",
            "GET",
            "/projects/{project}/reconcile",
            {"project": s},
            ("project",),
        ),
        tool(
            "get_waves",
            "The order the slices may be worked in, computed from the "
            "dependency graph rather than chosen. Slices in one wave have no "
            "edge between them, so they can be worked in parallel with "
            "nothing to coordinate; every earlier wave is complete before the "
            "next begins, so each reads its dependencies as frozen. A "
            "cross-cutting slice lands in wave 0. `chain: true` means every "
            "wave is one slice wide -- nothing can be done in parallel, and "
            "the slices are probably too coupled. `unschedulable` is only "
            "non-empty when the gates have already refused the graph.",
            "GET",
            "/projects/{project}/waves",
            {"project": s},
            ("project",),
        ),
        tool(
            "propose_slicing",
            "The graph half of the grouping problem, before any slice "
            "exists: which behaviours share an intent parent, and how far "
            "each parent spreads. A parent whose children scatter widely is "
            "either constraint-shaped (a cross-cutting tell) or evidence the "
            "cut runs across the grain of intent -- two opposite readings, "
            "which is why it is reported rather than acted on. Grouping by "
            "shared nouns is YOUR job: this unit never looks inside a body.",
            "GET",
            "/projects/{project}/slicing/candidates",
            {"project": s},
            ("project",),
        ),
        tool(
            "score_slicing",
            "Score a proposed partition WITHOUT creating it. `proposal` maps "
            "slice name to a list of entry ids; `types` optionally marks one "
            "cross_cutting. Runs every gate and every structural metric as "
            "though the cut were real, and commits nothing -- which is what "
            "makes trialling several slicings possible, since slices split "
            "and never merge. `legal` is computed; good is not. Nothing here "
            "gates or refuses anything.",
            "POST",
            "/projects/{project}/slicing/score",
            {
                "project": s,
                "proposal": {"type": "object"},
                "types": {"type": "object"},
            },
            ("project", "proposal"),
        ),
        tool(
            "get_insights",
            "Everything measurable about how a project has gone. "
            "`change_locality` is the primary score and is not a proxy: a "
            "good slicing is one where a typical change lands inside one "
            "slice, and every request already records exactly which entries "
            "it produced. `corrections` is where defects entered, which "
            "reads as a pipeline diagnostic -- clustered at intent means the "
            "interview was shallow. Issue pairs measure co-change, but are "
            "endogenous: that history was produced under one particular cut. "
            "Inputs to a judgement, never a judgement.",
            "GET",
            "/projects/{project}/insights",
            {"project": s},
            ("project",),
        ),
        tool(
            "list_events",
            "The project lifecycle in order -- requests as they were "
            "actually worded, derivations, corrections and the layer each "
            "entered at, refusals, and every assumption an agent had to make "
            "because it could not derive something. This is what the graph "
            "cannot tell you: a gate that failed and was then fixed leaves "
            "no trace in the fixed graph. Pass `since` (a seq) to page. "
            "Analysis, not operation -- never load this during ordinary work.",
            "GET",
            "/projects/{project}/events",
            {"project": s, "kind": s, "since": {"type": "integer"}},
            ("project",),
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
            "declare_module",
            "Record which spec entries a module implements -- the bottom "
            "layer's backlink. Code is not an entry in this graph, so this is "
            "the only thing tying a file to the reasoning that produced it. "
            "Replaces rather than merges: declaring an empty list removes the "
            "module. Only spec identifiers are accepted; a module claiming an "
            "architecture entry has skipped the layer that says how.",
            "POST",
            "/projects/{project}/modules",
            {"project": s, "path": s, "implements": strings},
            ("project", "path", "implements"),
        ),
        tool(
            "list_modules",
            "Every module backlink, plus `unimplemented`: the spec entries no "
            "module claims yet.",
            "GET",
            "/projects/{project}/modules",
            {"project": s},
            ("project",),
        ),
        tool(
            "get_plan",
            "The spec-level diff since a mark, resolved into a write set "
            "(modules implementing a changed entry -- editable) and a read "
            "set (modules that consumed a changed meaning but did not "
            "themselves change -- context only). `since` is the `mark` a "
            "previous plan returned; omit it for a first iteration, where the "
            "whole spec layer is the diff. This is the planner's input, and "
            "deliberately NOT a git diff: reasoning from code makes code the "
            "source of truth and the spec layer decorative within a few "
            "cycles. Refused while the graph is unsound.",
            "GET",
            "/projects/{project}/plan",
            {"project": s, "since": {"type": "integer"}},
            ("project",),
        ),
        tool(
            "audit_diff",
            "Compare the files actually touched against the write set that "
            "was declared. This is where a git diff belongs -- afterwards, as "
            "evidence rather than as input. YOU run git and pass the paths in "
            "`touched`; this unit never executes anything. Pass "
            "`editable_paths`, or pass `since` and let it derive them. A file "
            "touched outside the set is a gate failure: either the planner "
            "missed a dependency or the executor freelanced.",
            "POST",
            "/projects/{project}/audit",
            {
                "project": s,
                "touched": strings,
                "editable_paths": strings,
                "since": {"type": "integer"},
            },
            ("project", "touched"),
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
    ("GET", re.compile(rf"^/projects/{_P}/waves$"), "waves"),
    # The internal queue: one part of the pipeline asking another for a fix.
    ("POST", re.compile(rf"^/projects/{_P}/issues$"), "raise_issue"),
    ("GET", re.compile(rf"^/projects/{_P}/issues$"), "list_issues"),
    (
        "POST",
        re.compile(rf"^/projects/{_P}/issues/(?P<iid>[A-Za-z0-9_-]+)/close$"),
        "close_issue",
    ),
    ("GET", re.compile(rf"^/projects/{_P}/reconcile$"), "reconcile"),
    # Analysis. Reported, never enforced -- no gate fires on any of it.
    ("GET", re.compile(rf"^/projects/{_P}/slicing/candidates$"), "candidates"),
    ("POST", re.compile(rf"^/projects/{_P}/slicing/score$"), "score_slicing"),
    ("GET", re.compile(rf"^/projects/{_P}/insights$"), "insights"),
    ("GET", re.compile(rf"^/projects/{_P}/events$"), "events"),
    ("GET", re.compile(rf"^/projects/{_P}/entries/(?P<id>{_ID})$"), "entry"),
    ("GET", re.compile(rf"^/projects/{_P}/work-package$"), "work_package"),
    # Spec to code.
    ("POST", re.compile(rf"^/projects/{_P}/modules$"), "declare_module"),
    ("GET", re.compile(rf"^/projects/{_P}/modules$"), "list_modules"),
    ("GET", re.compile(rf"^/projects/{_P}/plan$"), "plan"),
    ("POST", re.compile(rf"^/projects/{_P}/audit$"), "audit"),
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
    issues: IssueLog | None = None,
    events: Lifecycle | None = None,
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
    issues = issues or IssueLog(store.issues_path())
    # A copy of every recorded event goes to whichever unit holds the logs
    # role, addressed by role and best-effort. The local log is the durable
    # record; this is for whoever aggregates across units.
    events = events or Lifecycle(
        store.events_path(), sink=lambda e: ship(store.root(), e)
    )
    try:
        if name == "health":
            return 200, JSON, json.dumps({"status": "ok"})

        if name == "stats":
            return (
                200,
                JSON,
                json.dumps(
                    {
                        "unit": UNIT_NAME,
                        "computed_at": now_fn(),
                        "metrics": service.unit_metrics(store, inbox, issues),
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
            return 201, JSON, json.dumps(service.post_to_inbox(inbox, body, now_fn, events))

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

        if name == "candidates":
            return (
                200,
                JSON,
                json.dumps(service.propose_slicing(store, project)),
            )

        if name == "score_slicing":
            return (
                200,
                JSON,
                json.dumps(service.score_slicing(store, project, body or {})),
            )

        if name == "insights":
            return (
                200,
                JSON,
                json.dumps(service.get_insights(store, inbox, issues, project)),
            )

        if name == "events":
            return (
                200,
                JSON,
                json.dumps(service.list_events(events, project, query)),
            )

        if name == "raise_issue":
            return (
                201,
                JSON,
                json.dumps(
                    service.raise_issue(store, issues, project, body or {}, now_fn, events)
                ),
            )

        if name == "list_issues":
            return 200, JSON, json.dumps(service.list_issues(issues, project, query))

        if name == "close_issue":
            return (
                200,
                JSON,
                json.dumps(
                    service.close_issue(issues, params["iid"], body or {})
                ),
            )

        if name == "reconcile":
            return (
                200,
                JSON,
                json.dumps(service.get_reconciliation(store, issues, project)),
            )

        if name == "waves":
            return 200, JSON, json.dumps(service.get_waves(store, project))

        if name == "gates":
            return 200, JSON, json.dumps(service.check_gates(store, project))

        if name == "entry":
            return (
                200,
                JSON,
                json.dumps(service.get_entry(store, project, params["id"])),
            )

        if name == "amendment":
            result = service.submit_amendment(
                store, inbox, project, body, events, now_fn
            )
            return (200 if result["admitted"] else 409), JSON, json.dumps(result)

        if name == "classify_slice":
            result = service.classify_slice(
                store, project, params["slice"], body or {}, events, now_fn
            )
            return (200 if result["recorded"] else 409), JSON, json.dumps(result)

        if name == "declare_module":
            return (
                200,
                JSON,
                json.dumps(service.declare_module(store, project, body or {})),
            )

        if name == "list_modules":
            return 200, JSON, json.dumps(service.list_modules(store, project))

        if name == "plan":
            result = service.get_plan(store, project, query.get("since"))
            return (200 if result["issued"] else 409), JSON, json.dumps(result)

        if name == "audit":
            return (
                200,
                JSON,
                json.dumps(service.audit_diff(store, project, body or {})),
            )

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
