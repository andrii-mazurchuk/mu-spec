"""The operations the unit actually offers, as plain functions over a store.

Two doors, with deliberately different powers.

**The inbox** is the outside world's only way in. It records requests, never
writes. What a request may reach is decided by its type, so a caller cannot
express "edit the spec" at all.

**Amendments** are the pipeline's own write path, used by the agent doing
propagation. They reach any layer -- but every amendment must cite the inbox
message it serves, so nothing enters the graph that nobody asked for, and
every entry traces out past the graph to the human who wanted it.

Neither door is raw CRUD over entries. That would let a caller assemble a
graph that does not hold together, which is the thing this unit exists to
prevent.

No HTTP in this module. Every function takes a store and returns a plain
dict, so the whole surface is testable without a socket.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from mu_spec.gates import BAD_DEPENDENCY, ORPHAN, admission_gates
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import LAYERS, Identifier, InvalidIdentifier, parse, sort_key
from mu_spec.inbox import ACCEPTED, TYPES, Inbox, InboxError
from mu_spec.issues import OPEN, RESOLVED, IssueError, IssueLog
from mu_spec.reconcile import route
from mu_spec.slice_gates import BAD_EMISSION, edge_gates, slice_gates
from mu_spec.storage import SLICE_TYPES, Manifest, ProjectStore, Slice
from mu_spec.waves import schedule

COMMENTS_FILE = "comments.jsonl"

# Everything except `unserved`. Being incomplete is the ordinary state of a
# project mid-propagation; everything else here means the graph is wrong now.
_BLOCKING = (ORPHAN, BAD_DEPENDENCY, BAD_EMISSION)


class ServiceError(ValueError):
    """A caller error -- bad layer, unknown entry, a write that would break
    the graph. Carries a message meant to be read by whoever called."""


# -- helpers ----------------------------------------------------------------


def _entry_view(entry: Entry, full: bool) -> dict[str, Any]:
    view = {
        "id": str(entry.id),
        "layer": entry.id.layer_name,
        "title": entry.display_title,
        "derives_from": [str(d) for d in entry.derives_from],
        "depends_on": [str(d) for d in entry.depends_on],
        "emits_into": [str(d) for d in entry.emits_into],
    }
    if full:
        view["body"] = entry.body
    return view


def _gate_report(graph: Graph, manifest: Manifest) -> dict[str, Any]:
    """Two different questions, reported separately because they have
    different consequences.

    `sound` -- no orphans, no broken horizontal edge, no slice-level finding.
    A graph containing a claim that derives from nothing, that needs
    something retired or nonexistent, or that is cut into slices which need
    each other, is broken now. This blocks: amendments are refused and work
    packages are not issued.

    `complete` -- nothing unserved. Knowledge has been carried all the way
    down to spec on every branch. This does NOT block; being incomplete is
    the ordinary state of a project mid-propagation, and it is the report of
    what is left to do rather than a defect.

    The two lists stay apart because they are about different things: an
    entry-level finding names an identifier, a slice-level one names a slice,
    and flattening them would mean one of the two carried a null where the
    other carries the thing you need to go and fix.
    """
    findings = sorted(
        admission_gates(graph) + edge_gates(manifest, graph),
        key=lambda f: (sort_key(f.id), f.kind),
    )
    slice_findings = slice_gates(manifest, graph)
    return {
        "sound": not any(f.kind in _BLOCKING for f in findings)
        and not slice_findings,
        "complete": not findings,
        "clean": not findings and not slice_findings,
        "findings": [
            {"kind": f.kind, "id": str(f.id), "detail": f.detail} for f in findings
        ],
        "slice_findings": [
            {"kind": f.kind, "slice": f.slice, "detail": f.detail}
            for f in slice_findings
        ],
    }


def _parse_ids(raw: Any, field: str) -> tuple[Identifier, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ServiceError(f"{field} must be a list of identifiers")
    try:
        return tuple(parse(str(x)) for x in raw)
    except InvalidIdentifier as exc:
        raise ServiceError(f"{field}: {exc}") from exc


# -- the single external door: the inbox ------------------------------------


def post_to_inbox(inbox: Inbox, body: dict, now_fn: Callable[[], float]) -> dict:
    """Record what someone wants. Nothing in the graph changes here."""
    message = inbox.post(body, now_fn)
    spec = TYPES[message.type]
    return {
        "message_id": message.id,
        "status": message.status,
        "type": message.type,
        "may_originate_at": list(spec.originates_at),
        "next": (
            "an agent will read this from the inbox and decide what changes"
            if spec.originates_at
            else "recorded; this type never changes the graph on its own"
        ),
    }


def list_inbox(inbox: Inbox, query: dict) -> dict:
    messages = inbox.list(
        status=query.get("status"),
        project=query.get("project"),
        kind=query.get("type"),
        target=query.get("target"),
    )
    return {"messages": [m.to_json() for m in messages]}


def get_message(inbox: Inbox, message_id: str) -> dict:
    message = inbox.get(message_id)
    if message is None:
        raise ServiceError(f"unknown message {message_id!r}")
    return message.to_json()


def resolve_message(inbox: Inbox, message_id: str, body: dict) -> dict:
    """Close a request: accepted (and here is what it produced) or rejected
    (and here is why). Leaving it pending is what keeps the queue honest."""
    try:
        message = inbox.resolve(
            message_id,
            body.get("status", ACCEPTED),
            body.get("note", ""),
            body.get("produced", []),
        )
    except InboxError as exc:
        raise ServiceError(str(exc)) from exc
    return message.to_json()


def create_project(store: ProjectStore, inbox: Inbox, body: dict) -> dict:
    """Create an empty project. Only an `initiate` message authorises this,
    and the intent entries themselves arrive as a normal amendment -- the
    agent has to interview and derive them, not lift them verbatim out of
    whatever the requester happened to type."""
    project = body.get("project")
    if not isinstance(project, str) or not project.strip():
        raise ServiceError("'project' is required")
    message = _require_message(inbox, body, expected_types=("initiate",))
    store.create_project(project)
    inbox.record_produced(message.id, [f"project:{project}"])
    return {
        "project": project,
        "in_response_to": message.id,
        "next": "submit intent entries as an amendment citing the same message",
    }


def _require_message(inbox: Inbox, body: dict, expected_types=None):
    """Every write cites the request that authorised it. An amendment nobody
    asked for is refused -- this is the mechanical brake on an autonomous
    agent quietly adding scope."""
    message_id = body.get("in_response_to")
    if not message_id:
        raise ServiceError(
            "'in_response_to' is required -- every change must cite the inbox "
            "message that asked for it"
        )
    message = inbox.get(str(message_id))
    if message is None:
        raise ServiceError(f"unknown message {message_id!r}")
    if expected_types and message.type not in expected_types:
        raise ServiceError(
            f"{message.id} is a {message.type!r} message; expected one of "
            f"{expected_types}"
        )
    return message


# -- the propagation write path ---------------------------------------------


def submit_amendment(
    store: ProjectStore, inbox: Inbox, project: str, body: dict
) -> dict:
    """Record a batch of derived entries -- the result of an agent
    propagating a change downward.

    Validated as one transaction against the admission gates, and this is
    where "admission" is meant literally. The rule is asymmetric on purpose:

    - **Orphans block.** An entry that traces to nothing above, or points at
      something that doesn't exist or runs the wrong way, is never legitimate
      at any moment. Admitting one puts a lie in the graph.
    - **Unserved does not block.** An intent entry with no behaviour under it
      yet is the normal state halfway through propagation. It is reported as
      outstanding work, not treated as corruption.

    Blocking on both would make every legitimate propagation illegal; blocking
    on neither would make the gate decorative.

    Two further constraints, both about authority rather than structure. The
    amendment must cite the inbox message it serves, so nothing enters the
    graph that nobody asked for. And the FIRST entry created in response to a
    message must sit within that message type's permitted origination depth:
    a `correction` may start at intent or behaviour, never at spec. Once that
    origin exists, propagating downward from it is unrestricted -- the
    restriction is on where a change may *enter*, not how far it may travel.
    """
    message = _require_message(inbox, body)
    items = body.get("entries")
    if not isinstance(items, list) or not items:
        raise ServiceError("'entries' must be a non-empty list")
    slice_name = body.get("slice")

    existing = store.load_all(project)
    manifest = store.load_manifest(project)
    report_before = _gate_report(Graph(existing), manifest)
    before = {(f["kind"], f["id"]) for f in report_before["findings"]}
    before_slices = {
        (f["kind"], f["slice"]) for f in report_before["slice_findings"]
    }

    # Allocate against a copy of the high-water marks first, so a rejected
    # amendment does not burn identifiers.
    marks = dict(manifest.allocation)
    staged: list[Entry] = []
    for item in items:
        layer = item.get("layer")
        if layer not in LAYERS:
            raise ServiceError(f"entry layer must be one of {LAYERS}")
        if not item.get("title"):
            raise ServiceError("each entry needs a 'title'")
        marks[layer] = marks.get(layer, 0) + 1
        staged.append(
            Entry(
                id=Identifier(layer=layer, number=marks[layer]),
                derives_from=_parse_ids(item.get("derives_from"), "derives_from"),
                depends_on=_parse_ids(item.get("depends_on"), "depends_on"),
                emits_into=_parse_ids(item.get("emits_into"), "emits_into"),
                title=item["title"],
                body=item.get("body", ""),
                supersedes=(
                    _parse_ids([item["supersedes"]], "supersedes")[0]
                    if item.get("supersedes")
                    else None
                ),
            )
        )

    spec_type = TYPES[message.type]
    already = (message.resolution or {}).get("produced", [])
    is_origination = not any(a[:1] in ("I", "B", "A", "S") and "·" in a for a in already)
    if is_origination:
        if not spec_type.originates_at:
            raise ServiceError(
                f"a {message.type!r} message never creates entries -- it "
                "records something to consider, and changes nothing on its own"
            )
        topmost = min(staged, key=lambda e: e.id.depth).id
        if topmost.layer not in spec_type.originates_at:
            raise ServiceError(
                f"a {message.type!r} message may only originate at "
                f"{spec_type.originates_at}, but this amendment starts at "
                f"{topmost.layer_name} ({topmost}). Fixing something lower "
                "while the layers above still say the old thing is how the "
                "artifacts start lying -- classify the defect upward first."
            )

    prospective = Graph(existing + staged)

    # The manifest-aware checks need the membership this amendment would
    # create, which does not exist yet -- membership is recorded on write,
    # and the write is what is being decided. So they run against a copy of
    # the manifest with the staged identifiers already filed. Deciding on the
    # current manifest instead would mean an amendment could only ever be
    # caught breaking the structure one write *after* it broke it.
    prospective_manifest = Manifest.from_json(manifest.to_json())
    if slice_name:
        target = prospective_manifest.slices.setdefault(
            slice_name, Slice(name=slice_name)
        )
        target.members.update(e.id for e in staged)

    new_orphans = [
        f
        for f in admission_gates(prospective)
        + edge_gates(prospective_manifest, prospective)
        if f.kind in _BLOCKING and (f.kind, str(f.id)) not in before
    ]

    # Retiring an entry necessarily strands whatever derived from it, and
    # that is not a defect in the amendment -- it is the amendment doing its
    # job. A correction to B*01 is *supposed* to leave the architecture and
    # spec beneath it stale; the blast radius is how you learn what to
    # re-derive, and the graph stays unsound until you do, which is what
    # withholds work packages in the meantime.
    #
    # So the two kinds of new orphan are separated. Stranded-by-this-
    # supersession is admitted and reported. Anything else -- a reference to
    # something that never existed, or one pointing the wrong way -- is
    # refused, because no amendment ever has a reason to introduce one.
    retired = {str(e.supersedes) for e in staged if e.supersedes is not None}
    stale, blocking = [], []
    for finding in new_orphans:
        reasons = finding.detail.split("; ")
        if reasons and all(
            any(f"{r} is superseded by" in reason for r in retired) for reason in reasons
        ):
            stale.append(finding)
        else:
            blocking.append(finding)

    if blocking:
        return {
            "admitted": False,
            "reason": "amendment would introduce a broken edge",
            "findings": [
                {"kind": f.kind, "id": str(f.id), "detail": f.detail}
                for f in blocking
            ],
            "slice_findings": [],
        }

    new_slice_findings = [
        f
        for f in slice_gates(prospective_manifest, prospective)
        if (f.kind, f.slice) not in before_slices
    ]
    if new_slice_findings:
        return {
            "admitted": False,
            "reason": "amendment would break the slice structure",
            "findings": [],
            "slice_findings": [
                {"kind": f.kind, "slice": f.slice, "detail": f.detail}
                for f in new_slice_findings
            ],
        }

    for entry in staged:
        store.allocate(project, entry.id.layer)
    store.append(project, staged, slice_name=slice_name)
    created = [str(e.id) for e in sorted(staged, key=lambda e: sort_key(e.id))]
    inbox.record_produced(message.id, created)

    after = store.load_graph(project)
    return {
        "admitted": True,
        "created": created,
        "in_response_to": message.id,
        # What this amendment stranded by retiring something. Each of these
        # must be re-derived; until then the graph is unsound and no work
        # package will be issued.
        "stale_references": [
            {"id": str(f.id), "detail": f.detail} for f in stale
        ],
        "blast_radius": [
            str(i)
            for i in after.blast_radius(
                [e.supersedes for e in staged if e.supersedes is not None]
            )
        ],
        "gates": _gate_report(after, store.load_manifest(project)),
    }


def classify_slice(
    store: ProjectStore, project: str, slice_name: str, body: dict
) -> dict:
    """Record whether a slice is ordinary or cross-cutting.

    A judgement the unit does not make. The agent argues it from the two
    tests -- does a caller branch on what comes back, and does the contract
    name a domain object someone else owns -- and a human rules on it. What
    the unit does is record the ruling and make it structural, because the
    type is what decides whose context this slice's entries land in.

    Checked before it is written, and refused if the ruling would contradict
    edges that already exist. A slice already reaching into a feature slice
    cannot be declared cross-cutting after the fact -- otherwise this is a
    back door to the state the amendment path refuses to create, reached by
    building the edges first and relabelling afterwards.
    """
    requested = body.get("type", "")
    manifest = store.load_manifest(project)
    if requested not in SLICE_TYPES:
        raise ServiceError(
            f"unknown slice type {requested!r}, expected one of {SLICE_TYPES}"
        )
    if slice_name not in manifest.slices:
        raise ServiceError(f"unknown slice {slice_name!r}")

    graph = store.load_graph(project)
    before = {(f.kind, f.slice) for f in slice_gates(manifest, graph)}
    prospective = Manifest.from_json(manifest.to_json())
    prospective.slices[slice_name].type = requested
    conflicts = [
        f
        for f in slice_gates(prospective, graph)
        if (f.kind, f.slice) not in before
    ]
    if conflicts:
        return {
            "project": project,
            "slice": slice_name,
            "recorded": False,
            "reason": "the ruling contradicts edges that already exist",
            "slice_findings": [
                {"kind": f.kind, "slice": f.slice, "detail": f.detail}
                for f in conflicts
            ],
        }

    store.set_slice_type(project, slice_name, requested)
    manifest = store.load_manifest(project)
    return {
        "project": project,
        "slice": slice_name,
        "recorded": True,
        "type": manifest.slices[slice_name].type,
        "cross_cutting": list(manifest.cross_cutting()),
    }


# -- reading ----------------------------------------------------------------


def get_spine(store: ProjectStore, project: str, layer: str | None) -> dict:
    """What an agent loads unconditionally before deciding what it needs.
    Identifier, one-line title, derives-from -- no bodies."""
    graph = store.load_graph(project)
    manifest = store.load_manifest(project)
    rows = graph.spine()
    if layer:
        if layer not in LAYERS:
            raise ServiceError(f"'layer' must be one of {LAYERS}")
        rows = [r for r in rows if r["id"].startswith(layer)]
    return {
        "project": project,
        "spine": [
            {**row, "slice": manifest.slice_of(parse(row["id"]))} for row in rows
        ],
    }


def get_entry(store: ProjectStore, project: str, identifier: str) -> dict:
    ident = _parse_ids([identifier], "id")[0]
    graph = store.load_graph(project)
    entry = graph.get(ident)
    if entry is None:
        raise ServiceError(f"{ident} does not exist")
    view = _entry_view(entry, full=True)
    view["superseded_by"] = (
        str(graph.superseded_by(ident)) if graph.superseded_by(ident) else None
    )
    view["children"] = [str(i) for i in graph.children(ident)]
    return view


# -- the internal queue -----------------------------------------------------


def raise_issue(
    store: ProjectStore,
    issues: IssueLog,
    project: str,
    body: dict,
    now_fn: Callable[[], float],
) -> dict:
    """File a request against an artifact.

    An agent that finds another slice's entry wrong or missing files this and
    proceeds on its stated assumption. It does not message that slice's agent,
    because there is no such channel: that agent is finished, and a live one
    would mean blocking or nondeterminism, either of which loses the audit
    property.

    The target's slice is resolved and stored here, at filing time, so
    grouping later does not depend on membership that may since have moved.
    """
    manifest = store.load_manifest(project)
    target_slice = None
    raw_target = body.get("target")
    if isinstance(raw_target, str) and raw_target.strip():
        try:
            target_slice = manifest.slice_of(parse(raw_target))
        except InvalidIdentifier as exc:
            raise ServiceError(f"'target': {exc}") from exc
    try:
        issue = issues.raise_issue(
            {**body, "project": project}, target_slice, now_fn
        )
    except IssueError as exc:
        raise ServiceError(str(exc)) from exc
    return {
        **issue.to_json(),
        "next": "proceed on your assumption; this is repaired in the next "
        "reconciliation, not now",
    }


def list_issues(issues: IssueLog, project: str, query: dict) -> dict:
    rows = issues.list(
        project=project,
        status=query.get("status"),
        target_slice=query.get("slice"),
    )
    return {"project": project, "issues": [i.to_json() for i in rows]}


def close_issue(issues: IssueLog, issue_id: str, body: dict) -> dict:
    try:
        issue = issues.close(
            issue_id,
            body.get("status", RESOLVED),
            body.get("note", ""),
            body.get("produced", []),
        )
    except IssueError as exc:
        raise ServiceError(str(exc)) from exc
    return issue.to_json()


def get_reconciliation(store: ProjectStore, issues: IssueLog, project: str) -> dict:
    """The open queue, grouped into one repair batch per target slice.

    Run after every wave rather than once per layer: a smaller blast radius,
    and failures caught while the context that produced them is still narrow.

    Each batch carries its own re-run scope -- the entries that consumed a
    meaning one of its issues says has moved, computed at entry level from
    the entries' own edges. Batches come back in dependency order.

    `escalations` are the issues that are not repairs: past the round cap, or
    semantically reaching back into a wave that is already finished. Those go
    to a human. Nothing here is dispatched -- this unit produces the batches
    as data, and whatever runs a repair session lives elsewhere.
    """
    manifest = store.load_manifest(project)
    graph = store.load_graph(project)
    batches, escalations = route(
        manifest, graph, issues.list(project=project, status=OPEN)
    )
    return {
        "project": project,
        "batches": [b.to_json() for b in batches],
        "escalations": [
            {"issue": e.issue, "reason": e.reason, "detail": e.detail}
            for e in escalations
        ],
        "next": (
            "run one repair session per batch, in the order given"
            if batches
            else "nothing open"
        ),
    }


def get_waves(store: ProjectStore, project: str) -> dict:
    """The order the slices may be worked in, and what may be worked at once.

    Computed from the projected dependency graph, never chosen. Slices in one
    wave have no edge between them structurally, so they can be worked in
    parallel with nothing to coordinate and nothing to lock -- and every wave
    below is finished before the next begins, so each agent reads its
    dependencies as frozen.

    `unschedulable` is only ever non-empty on a graph the admission gates
    have already refused; it is reported rather than raised so the caller can
    see which slices are caught rather than just that something is wrong.
    """
    manifest = store.load_manifest(project)
    graph = store.load_graph(project)
    sched = schedule(manifest, graph)
    return {
        "project": project,
        "waves": [
            {
                "wave": number,
                "slices": list(wave),
                # Repeated per wave because this is what a scheduler acts on:
                # how many agents it may run at once, right here.
                "width": len(wave),
            }
            for number, wave in enumerate(sched.waves)
        ],
        "wave_of": sched.wave_of(),
        "unschedulable": list(sched.unschedulable),
        # Every wave one slice wide means nothing can be done in parallel --
        # the slices are too coupled. Reported, never acted on.
        "chain": sched.chain,
    }


def check_gates(store: ProjectStore, project: str) -> dict:
    return {
        "project": project,
        **_gate_report(store.load_graph(project), store.load_manifest(project)),
    }


# -- 5. retrieve the final layer, for producing code ------------------------


def get_work_package(store: ProjectStore, project: str, slice_name: str) -> dict:
    """The bounded, declared context a coding agent is given -- and the whole
    reason the graph exists.

    Four parts, each with a different permission and a different cost:

    - **write set** -- the spec entries of this slice, with full bodies. The
      only thing the executor may change.
    - **justification** -- why each of those exists. The direct parent gets a
      full body; everything further up is spine only. An executor needs the
      architectural decision in full and merely needs to know the behaviour
      and intent above it exist.
    - **read set** -- spec entries from the slices this one depends on,
      spine only. Read-only context. Those dependencies are projected from
      the entries' own edges rather than declared beside them, so the read
      set is exactly what the work actually needs and cannot drift from it.
      This is what turns "peeking at related features" from the executor
      wandering the repo into a bounded, computed operation.
    - **cross-cutting** -- every cross-cutting slice's spec entries, spine
      only, whether or not this slice depends on one. Their behaviour ranges
      over other slices rather than naming a subject of its own, so the
      dependency is real, universal, and never worth declaring n times.

    Refused when the graph is *unsound* -- when it contains an orphan.
    Handing an executor a package built from a broken chain produces code
    derived from a lie, and the failure surfaces much later and much more
    expensively.

    Not refused merely for being *incomplete*. Another slice still being
    propagated says nothing about whether this one is ready, and blocking on
    it would mean no slice could ever ship until every slice was finished.
    """
    manifest = store.load_manifest(project)
    if slice_name not in manifest.slices:
        raise ServiceError(f"unknown slice {slice_name!r}")

    graph = store.load_graph(project)
    gates = _gate_report(graph, manifest)
    if not gates["sound"]:
        return {
            "project": project,
            "slice": slice_name,
            "issued": False,
            "reason": "graph is unsound -- it contains orphaned entries",
            "gates": gates,
        }

    members = manifest.slices[slice_name].members
    write_set = [
        e for e in graph.entries() if e.id.layer == "S" and e.id in members
    ]

    justification: dict[str, list[dict]] = {}
    for entry in write_set:
        direct = set(entry.derives_from)
        chain = []
        for ident in graph.ancestors(entry.id):
            parent = graph.get(ident)
            if parent is not None:
                chain.append(_entry_view(parent, full=ident in direct))
        justification[str(entry.id)] = chain

    read_set = []
    for dep in manifest.dependency_graph(graph).get(slice_name, ()):
        dep_slice = manifest.slices.get(dep)
        if dep_slice is None:
            continue
        for entry in graph.entries():
            if entry.id.layer == "S" and entry.id in dep_slice.members:
                view = _entry_view(entry, full=False)
                view["slice"] = dep
                read_set.append(view)

    cross = []
    for name in manifest.cross_cutting():
        if name == slice_name:
            continue
        members = manifest.slices[name].members
        for entry in graph.entries():
            if entry.id.layer == "S" and entry.id in members:
                view = _entry_view(entry, full=False)
                view["slice"] = name
                cross.append(view)

    return {
        "project": project,
        "slice": slice_name,
        "issued": True,
        "write_set": [_entry_view(e, full=True) for e in write_set],
        "justification": justification,
        "read_set": read_set,
        "cross_cutting": cross,
        "audit": {
            "editable_ids": [str(e.id) for e in write_set],
            "rule": "any file touched that does not declare one of editable_ids "
            "is a gate failure -- either the planner missed a dependency or "
            "the executor freelanced",
        },
    }


# -- 6. review the final layer ----------------------------------------------


def review_layer(
    store: ProjectStore,
    inbox: Inbox,
    project: str,
    layer: str,
    slice_name: str | None,
) -> dict:
    """Read a layer with each entry's justification chain attached, so a
    reviewer sees the decision and what it claims to serve in one place
    rather than reading whole documents."""
    if layer not in LAYERS:
        raise ServiceError(f"'layer' must be one of {LAYERS}")
    manifest = store.load_manifest(project)
    graph = store.load_graph(project)

    members = None
    if slice_name:
        if slice_name not in manifest.slices:
            raise ServiceError(f"unknown slice {slice_name!r}")
        members = manifest.slices[slice_name].members

    rows = []
    for entry in graph.entries():
        if entry.id.layer != layer:
            continue
        if members is not None and entry.id not in members:
            continue
        rows.append(
            {
                **_entry_view(entry, full=True),
                "slice": manifest.slice_of(entry.id),
                "derives_from_titles": [
                    {"id": str(i), "title": graph.get(i).display_title}
                    for i in entry.derives_from
                    if graph.get(i) is not None
                ],
                "served_by": [str(i) for i in graph.children(entry.id)],
                # Comments are inbox messages, not a second store. A comment
                # is a request that changes nothing, which is exactly what an
                # annotation is -- a parallel log would have meant two places
                # to look for "what did someone say about this".
                "comments": [
                    m.to_json()
                    for m in inbox.list(kind="comment", target=str(entry.id))
                ],
            }
        )

    return {
        "project": project,
        "layer": layer,
        "slice": slice_name,
        "entries": rows,
        "gates": _gate_report(graph, manifest),
    }
