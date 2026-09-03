"""The operations the unit actually offers, as plain functions over a store.

Deliberately task-shaped, not data-shaped. There is no "create entry" here:
the surface is the six things a caller genuinely wants to do -- start a
project, add a feature, fix a defect, comment, retrieve work, review work --
plus the machinery those need. Exposing raw CRUD over entries would let a
caller build a graph that doesn't hold together, which is the thing this
unit exists to prevent.

No HTTP in this module. Every function takes a store and returns a plain
dict, so the whole surface is testable without a socket.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from mu_spec.gates import ORPHAN, admission_gates
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import LAYERS, Identifier, InvalidIdentifier, parse, sort_key
from mu_spec.storage import CROSS_CUTTING, ProjectStore

COMMENTS_FILE = "comments.jsonl"


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
    }
    if full:
        view["body"] = entry.body
    return view


def _gate_report(graph: Graph) -> dict[str, Any]:
    """Two different questions, reported separately because they have
    different consequences.

    `sound` -- no orphans. A graph containing a claim that derives from
    nothing, or from something retired or nonexistent, is broken now. This
    blocks: amendments are refused and work packages are not issued.

    `complete` -- nothing unserved. Knowledge has been carried all the way
    down to spec on every branch. This does NOT block; being incomplete is
    the ordinary state of a project mid-propagation, and it is the report of
    what is left to do rather than a defect.
    """
    findings = admission_gates(graph)
    return {
        "sound": not any(f.kind == ORPHAN for f in findings),
        "complete": not findings,
        "clean": not findings,
        "findings": [
            {"kind": f.kind, "id": str(f.id), "detail": f.detail} for f in findings
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


# -- 1. initiate ------------------------------------------------------------


def initiate_project(store: ProjectStore, body: dict) -> dict:
    """Start a project by stating intent. This is where requirements end from
    the human side -- everything below is derived, never typed in here."""
    project = body.get("project")
    if not isinstance(project, str) or not project.strip():
        raise ServiceError("'project' is required")
    items = body.get("intent") or []
    if not isinstance(items, list) or not items:
        raise ServiceError("'intent' must be a non-empty list of {title, body}")

    store.create_project(project)
    created = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            raise ServiceError("each intent item needs a 'title'")
        ident = store.allocate(project, "I")
        store.append(
            project,
            [Entry(id=ident, title=item["title"], body=item.get("body", ""))],
        )
        created.append(str(ident))

    return {
        "project": project,
        "created": created,
        "gates": _gate_report(store.load_graph(project)),
        "next": "propagate intent into behaviour via submit_amendment",
    }


# -- 2. add a feature -------------------------------------------------------


def add_feature(store: ProjectStore, project: str, body: dict) -> dict:
    """A new feature originates at intent and enters as an amendment -- a new
    numbered entry, never an edit to existing text. It propagates downward
    afterwards; this call only records the requirement."""
    if not body.get("title"):
        raise ServiceError("'title' is required")
    ident = store.allocate(project, "I")
    store.append(
        project, [Entry(id=ident, title=body["title"], body=body.get("body", ""))]
    )
    return {
        "id": str(ident),
        "gates": _gate_report(store.load_graph(project)),
        "next": "this intent entry is unserved until behaviour derives from it",
    }


# -- 3. fix an old one ------------------------------------------------------


def report_defect(store: ProjectStore, project: str, body: dict) -> dict:
    """A correction is classified *before* it is fixed: the caller states
    which layer the error actually lives in, and the fix is applied there.

    Patching the symptom at the layer where it was noticed leaves the layers
    above still saying the wrong thing -- the artifacts then lie, and the next
    session reads the lie and reintroduces the bug.
    """
    layer = body.get("layer")
    if layer not in LAYERS:
        raise ServiceError(f"'layer' must be one of {LAYERS} -- classify the defect")
    if not body.get("title"):
        raise ServiceError("'title' is required")

    graph = store.load_graph(project)
    supersedes = None
    if body.get("supersedes"):
        supersedes = _parse_ids([body["supersedes"]], "supersedes")[0]
        if graph.get(supersedes) is None:
            raise ServiceError(f"{supersedes} does not exist")

    derives_from = _parse_ids(body.get("derives_from"), "derives_from")
    if not derives_from and supersedes is not None:
        # A replacement serves what the entry it retires served, unless the
        # caller says otherwise. Re-typing the parents is how they drift.
        derives_from = graph.parents(supersedes)

    ident = store.allocate(project, layer)
    slice_name = body.get("slice")
    if layer != "I" and not slice_name and supersedes is not None:
        slice_name = store.load_manifest(project).slice_of(supersedes)

    store.append(
        project,
        [
            Entry(
                id=ident,
                derives_from=derives_from,
                title=body["title"],
                body=body.get("body", ""),
                supersedes=supersedes,
            )
        ],
        slice_name=slice_name,
    )

    after = store.load_graph(project)
    radius = after.blast_radius([ident] if supersedes is None else [supersedes, ident])
    return {
        "id": str(ident),
        "supersedes": str(supersedes) if supersedes else None,
        "blast_radius": [str(i) for i in radius],
        "gates": _gate_report(after),
        "next": "re-derive every entry in blast_radius",
    }


# -- 4. comment -------------------------------------------------------------


def comment_on_entry(
    store: ProjectStore, project: str, body: dict, now_fn: Callable[[], float]
) -> dict:
    """A comment is an annotation, never an amendment. It does not enter the
    graph, does not derive from anything, and nothing derives from it -- so it
    can never satisfy a requirement or silently become a decision. It is a
    question or an observation attached to an entry."""
    target = body.get("target")
    if not target:
        raise ServiceError("'target' is required")
    ident = _parse_ids([target], "target")[0]
    if store.load_graph(project).get(ident) is None:
        raise ServiceError(f"{ident} does not exist")
    if not body.get("body"):
        raise ServiceError("'body' is required")

    record = {
        "target": str(ident),
        "author": body.get("author", "unknown"),
        "body": body["body"],
        "at": now_fn(),
    }
    path = store.comments_path(project)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return {"recorded": True, "target": str(ident)}


def list_comments(store: ProjectStore, project: str, target: str | None) -> dict:
    path = store.comments_path(project)
    if not path.exists():
        return {"comments": []}
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if target:
        records = [r for r in records if r["target"] == target]
    return {"comments": records}


# -- the propagation write path ---------------------------------------------


def submit_amendment(store: ProjectStore, project: str, body: dict) -> dict:
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
    """
    items = body.get("entries")
    if not isinstance(items, list) or not items:
        raise ServiceError("'entries' must be a non-empty list")
    slice_name = body.get("slice")

    existing = store.load_all(project)
    manifest = store.load_manifest(project)
    before = {(f["kind"], f["id"]) for f in _gate_report(Graph(existing))["findings"]}

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
                title=item["title"],
                body=item.get("body", ""),
                supersedes=(
                    _parse_ids([item["supersedes"]], "supersedes")[0]
                    if item.get("supersedes")
                    else None
                ),
            )
        )

    prospective = Graph(existing + staged)
    staged_ids = {e.id for e in staged}
    new_orphans = [
        f
        for f in admission_gates(prospective)
        if f.kind == ORPHAN and (f.kind, str(f.id)) not in before
    ]
    if new_orphans:
        return {
            "admitted": False,
            "reason": "amendment would introduce orphans",
            "findings": [
                {"kind": f.kind, "id": str(f.id), "detail": f.detail}
                for f in new_orphans
            ],
        }

    for entry in staged:
        store.allocate(project, entry.id.layer)
    store.append(project, staged, slice_name=slice_name)

    after = store.load_graph(project)
    return {
        "admitted": True,
        "created": [str(e.id) for e in sorted(staged, key=lambda e: sort_key(e.id))],
        "gates": _gate_report(after),
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
        rows = [r for r in rows if r[0].startswith(layer)]
    return {
        "project": project,
        "spine": [
            {
                "id": ident,
                "title": title,
                "derives_from": list(df),
                "slice": manifest.slice_of(parse(ident)),
            }
            for ident, title, df in rows
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


def check_gates(store: ProjectStore, project: str) -> dict:
    return {"project": project, **_gate_report(store.load_graph(project))}


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
    - **read set** -- spec entries from slices this one *declares* a
      dependency on, spine only. Read-only context. This is what turns
      "peeking at related features" from the executor wandering the repo into
      a bounded, declared operation.
    - **cross-cutting** -- spine only, and read-only by rule: a slice may
      declare that it emits into logging or notifications, never define them.

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
    gates = _gate_report(graph)
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
    for dep in manifest.slices[slice_name].depends_on:
        dep_members = manifest.slices.get(dep)
        if dep_members is None:
            continue
        for entry in graph.entries():
            if entry.id.layer == "S" and entry.id in dep_members.members:
                view = _entry_view(entry, full=False)
                view["slice"] = dep
                read_set.append(view)

    cross = []
    cc = manifest.slices.get(CROSS_CUTTING)
    if cc:
        cross = [
            _entry_view(e, full=False)
            for e in graph.entries()
            if e.id.layer == "S" and e.id in cc.members
        ]

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
    store: ProjectStore, project: str, layer: str, slice_name: str | None
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
                "comments": list_comments(store, project, str(entry.id))["comments"],
            }
        )

    return {
        "project": project,
        "layer": layer,
        "slice": slice_name,
        "entries": rows,
        "gates": _gate_report(graph),
    }
