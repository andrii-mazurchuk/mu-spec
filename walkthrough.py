"""End-to-end walkthrough of the pipeline, against a real HTTP server.

Run it and read the output top to bottom:

    python walkthrough.py            # temporary project, cleaned up by the OS
    python walkthrough.py --keep DIR # leave the files behind to inspect
    python walkthrough.py --http     # drive it over a real socket instead

By default it calls the request handler directly rather than binding a port.
Nothing is stubbed either way: `handle()` is the entire service, and the only
thing --http adds is the ~40-line transport wrapper around it. In-process is
the default because binding a listening socket is the one part of this that a
firewall or endpoint-protection policy can refuse, and a verification tool
that cannot run is worthless.

The point is to make the pipeline legible: you should be able to watch
knowledge enter as intent and arrive as a bounded work package, and see the
gates refuse the things they are supposed to refuse.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from mu_spec.server import build_handler
from mu_spec.storage import ProjectStore

BASE = ""
_STORE = None
_PROMPTS = Path("prompts")


def call(method: str, path: str, body: dict | None = None):
    """Over a socket when BASE is set, otherwise straight into the handler.
    Both return (status, parsed-json), so every step below is identical."""
    if not BASE:
        from mu_spec.server import handle

        status, _, raw = handle(method, path, _STORE, _PROMPTS, body)
        return status, json.loads(raw)

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def step(n: str, title: str) -> None:
    print(f"\n{'=' * 74}\n{n}  {title}\n{'=' * 74}")


def show(label: str, value) -> None:
    print(f"  {label}: {json.dumps(value, ensure_ascii=False)}")


def spine(project: str, note: str = "") -> None:
    _, payload = call("GET", f"/projects/{project}/spine")
    print(f"\n  SPINE {note}")
    print(
        f"  {'id':6} {'slice':11} {'derives':8} {'depends':8} {'emits':7} title"
    )
    print(f"  {'-' * 80}")
    for row in payload["spine"]:
        print(
            f"  {row['id']:6} {str(row['slice'] or '-'):11} "
            f"{','.join(row['derives_from']) or '-':8} "
            f"{','.join(row['depends_on']) or '-':8} "
            f"{','.join(row['emits_into']) or '-':7} {row['title']}"
        )


def gates(project: str) -> dict:
    _, payload = call("GET", f"/projects/{project}/gates")
    state = "SOUND" if payload["sound"] else "UNSOUND"
    state += " / COMPLETE" if payload["complete"] else " / incomplete"
    print(f"\n  GATES: {state}")
    for f in payload["findings"]:
        print(f"    - {f['kind']:14} {f['id']:7} {f['detail']}")
    for f in payload.get("slice_findings", []):
        print(f"    - {f['kind']:14} {f['slice']:7} {f['detail']}")
    if not payload["findings"] and not payload.get("slice_findings"):
        print("    (no findings)")
    return payload


def request(kind: str, title: str, project=None, **kw) -> str:
    """Ask for something. The only way in from outside."""
    body = {"type": kind, "title": title, **kw}
    if project:
        body["project"] = project
    status, payload = call("POST", "/inbox", body)
    print(
        f"  post_request({kind}) -> {status} {payload['message_id']} "
        f"[may originate at {payload['may_originate_at'] or 'nothing'}]"
    )
    return payload["message_id"]


def amend(project: str, mid: str, entries: list[dict], slice_name=None) -> dict:
    body = {"in_response_to": mid, "entries": entries}
    if slice_name:
        body["slice"] = slice_name
    status, payload = call("POST", f"/projects/{project}/amendments", body)
    verb = "ADMITTED" if payload.get("admitted") else "REFUSED"
    label = slice_name or "-"
    print(
        f"  submit_amendment({label}) -> {status} {verb} "
        f"{payload.get('created', '')}"
    )
    for f in payload.get("findings", []):
        print(f"    refused because: {f['kind']} {f['id']} -- {f['detail']}")
    for f in payload.get("stale_references", []):
        print(f"    stranded: {f['id']} -- {f['detail']}")
    if payload.get("error"):
        print(f"    refused because: {payload['error']}")
    return payload


def main(argv=None) -> int:
    global BASE, _STORE
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", default=None, help="write files here and leave them")
    parser.add_argument("--http", action="store_true", help="bind a real socket")
    parser.add_argument("--port", type=int, default=9107)
    args = parser.parse_args(argv)

    root = Path(args.keep) if args.keep else Path(tempfile.mkdtemp()) / "projects"
    root.mkdir(parents=True, exist_ok=True)
    store = ProjectStore(root)
    _STORE = store

    server = None
    if args.http:
        server = ThreadingHTTPServer(
            ("127.0.0.1", args.port), build_handler(store, Path("prompts"))
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        BASE = f"http://127.0.0.1:{args.port}"
        print(f"server up on {BASE}, storage at {root.resolve()}")
    else:
        print(f"in-process (no socket), storage at {root.resolve()}")

    P = "marketplace"

    # ---------------------------------------------------------------- 1
    step("1.", "A REQUEST — the only way in from outside")
    print("  Someone wants a marketplace. They do not name a layer, do not")
    print("  name an entry, and are not expected to know how any of this is")
    print("  laid out. They say what they want.\n")
    init = request(
        "initiate",
        "a marketplace where buyers find sellers and sellers get paid",
        project=P,
        origin="andrey",
    )
    _, queue = call("GET", "/inbox?status=pending")
    show("pending requests", [m["id"] + " " + m["type"] for m in queue["messages"]])

    step("1b.", "THE AGENT ACTS ON IT — project, then derived intent")
    print("  The request body is a raw idea. Intent entries are derived from")
    print("  it (in the real pipeline, after interviewing), never lifted")
    print("  verbatim -- and every write cites the request that authorised it.\n")
    status, payload = call(
        "POST", "/projects", {"project": P, "in_response_to": init}
    )
    show("create_project", status)
    amend(
        P,
        init,
        [
            {
                "layer": "I",
                "title": "Buyers can find the right seller quickly",
                "body": "Buyers abandon the marketplace when search is slow "
                "or returns irrelevant sellers.",
            },
            {
                "layer": "I",
                "title": "Sellers are paid within 24 hours of delivery",
                "body": "Late payment is the top reason sellers leave.",
            },
        ],
    )
    gates(P)
    print("\n  Both intent entries are unserved: stated, nothing built. That is")
    print("  the pipeline's to-do list, computed rather than tracked by hand.")

    # ---------------------------------------------------------------- 2
    step("2.", "PROPAGATE — behaviour derived from intent, by slice")
    amend(
        P,
        init,
        [
            {
                "layer": "B",
                "title": "A buyer can search sellers by keyword",
                "body": "Given a query, return sellers whose profile matches, "
                "ranked by relevance. Empty query returns nothing.",
                "derives_from": ["I·01"],
            },
            {
                "layer": "B",
                "title": "A buyer can filter results by delivery region",
                "body": "Filters narrow an existing result set; they never widen it.",
                "derives_from": ["I·01"],
            },
        ],
        slice_name="discovery",
    )
    amend(
        P,
        init,
        [
            {
                "layer": "B",
                "title": "A seller is paid once delivery is confirmed",
                "body": "Payment is released within 24h of confirmation. "
                "A disputed delivery holds payment.",
                "derives_from": ["I·02"],
            }
        ],
        slice_name="payouts",
    )
    spine(P, "after behaviour")
    gates(P)
    print("\n  Intent is now served. Behaviour is not -- knowledge has moved down")
    print("  one layer, and the gate says exactly how far it has got.")

    # ---------------------------------------------------------------- 3
    step("3.", "THE GATE REFUSES AN UNSOUND AMENDMENT")
    print("  An architecture entry claiming to derive from a behaviour that")
    print("  does not exist. Nothing is written, and no identifier is burned.\n")
    amend(
        P,
        init,
        [
            {"layer": "A", "title": "legitimate", "derives_from": ["B·01"]},
            {
                "layer": "A",
                "title": "derives from nothing real",
                "derives_from": ["B·99"],
            },
        ],
        slice_name="discovery",
    )
    _, after = call("GET", f"/projects/{P}/spine?layer=A")
    show("architecture entries after the refusal", [r["id"] for r in after["spine"]])

    # ---------------------------------------------------------------- 4
    step("4.", "PROPAGATE — architecture, then spec")
    amend(
        P,
        init,
        [
            {
                "layer": "A",
                "title": "Search runs against an inverted keyword index",
                "body": "A scan over sellers cannot meet the latency implied by "
                "B·01. An index is rebuilt on profile write.",
                "derives_from": ["B·01"],
            },
            {
                "layer": "A",
                "title": "Region filtering happens after ranking, in memory",
                "body": "Result sets are small once ranked, so filtering does not "
                "need its own index.",
                "derives_from": ["B·02"],
            },
        ],
        slice_name="discovery",
    )
    amend(
        P,
        init,
        [
            {
                "layer": "A",
                "title": "Payouts are driven by a delivery-confirmed event",
                "body": "Polling would breach the 24h window at scale.",
                "derives_from": ["B·03"],
            }
        ],
        slice_name="payouts",
    )
    amend(
        P,
        init,
        [
            {
                "layer": "S",
                "title": "search/index.py builds and queries the inverted index",
                "body": "Module search/index.py. build(sellers) -> Index; "
                "Index.query(text, limit) -> list[SellerId]. Stdlib only.",
                "derives_from": ["A·01"],
            },
            {
                "layer": "S",
                "title": "search/filters.py applies region filtering",
                "body": "Module search/filters.py. by_region(results, region) -> "
                "list[SellerId]. Pure function, no I/O.",
                "derives_from": ["A·02"],
            },
        ],
        slice_name="discovery",
    )
    amend(
        P,
        init,
        [
            {
                "layer": "S",
                "title": "payouts/release.py releases a payment on confirmation",
                "body": "Module payouts/release.py. on_delivery_confirmed(order) -> "
                "Payment | None. Returns None while a dispute is open. Resolves "
                "the seller through search/index.py rather than keeping its own "
                "lookup.",
                "derives_from": ["A·03"],
                "depends_on": ["S·01"],
            }
        ],
        slice_name="payouts",
    )
    spine(P, "fully propagated")
    gates(P)

    # ---------------------------------------------------------------- 5
    step("5.", "RETRIEVE THE FINAL LAYER — the coding agent's bounded context")
    request(
        "comment",
        "Is an inverted index overkill before we have 10k sellers?",
        project=P,
        targets=["A·01"],
        origin="andrey",
    )
    status, wp = call("GET", f"/projects/{P}/work-package?slice=discovery")
    show("status", status)
    show("issued", wp["issued"])
    print("\n  WRITE SET (full bodies -- the only thing the executor may edit)")
    for e in wp["write_set"]:
        print(f"    {e['id']}  {e['title']}")
        print(f"           {e['body'].strip().splitlines()[0]}")
    print("\n  JUSTIFICATION (why each exists; parent in full, above it spine only)")
    for spec_id, chain in wp["justification"].items():
        print(f"    {spec_id}:")
        for link in chain:
            depth = "full " if "body" in link else "spine"
            print(f"      [{depth}] {link['id']} {link['title']}")
    print("\n  READ SET (what this slice depends on -- context, not editable)")
    print(f"    {wp['read_set'] or '(none: nothing in discovery needs another slice)'}")

    _m = store.load_manifest(P)
    print("\n  SLICE DEPENDENCIES (projected from the entries, never authored)")
    for name, deps in _m.dependency_graph(store.load_graph(P)).items():
        print(f"    {name:12} -> {', '.join(deps) or '(nothing)'}")
    print("    Nothing in the manifest says this. S·03 depends on S·01, so")
    print("    payouts depends on discovery -- and the manifest has no field")
    print("    to disagree with the entries in.")
    print("\n  AUDIT RULE")
    show("editable_ids", wp["audit"]["editable_ids"])
    print(f"    {wp['audit']['rule']}")
    print("\n  Note what is NOT here: the payouts slice. The executor cannot see")
    print("  it, so it cannot accidentally couple to it.")

    # --------------------------------------------------------------- 5b
    step("5b.", "A CROSS-CUTTING SLICE — a concern nobody declares")
    print("  Audit logging is not a slice among the others. Nothing branches")
    print("  on what it returns, and its contract names no domain object -- it")
    print("  takes an actor, an action string and an opaque target. So it gets")
    print("  a full column of its own, and a type saying what it is.\n")
    audit_req = request(
        "feature", "every action has to be auditable", project=P, origin="andrey"
    )
    amend(
        P,
        audit_req,
        [
            {
                "layer": "I",
                "title": "Every action taken in the system is auditable",
                "body": "Disputes are unresolvable without a record of who did "
                "what, when.",
            }
        ],
    )
    intent_id = call("GET", f"/projects/{P}/spine?layer=I")[1]["spine"][-1]["id"]
    parent = intent_id
    for layer, title, body in (
        ("B", "Every state change is recorded with actor, action and time",
         "Applies to every slice. Ranges over their behaviour rather than "
         "owning a subject of its own."),
        ("A", "An append-only event log, written to and never read back",
         "Writers do not wait on it and never consume a result."),
        ("S", "audit/log.py appends one record per action",
         "Module audit/log.py. record(actor, action, target_id) -> None."),
    ):
        result = amend(
            P,
            audit_req,
            [{"layer": layer, "title": title, "body": body,
              "derives_from": [parent]}],
            slice_name="audit",
        )
        parent = result["created"][0]
    audit_spec = parent

    status, ruling = call(
        "POST", f"/projects/{P}/slices/audit/type", {"type": "cross_cutting"}
    )
    print(f"\n  classify_slice(audit) -> {status} {ruling.get('type')}")
    show("cross-cutting slices", ruling["cross_cutting"])

    print("\n  Now discovery emits into it. Fire-and-forget: no return value,")
    print("  so no ordering, so audit stays derivable before its emitters.")
    amend(
        P,
        audit_req,
        [
            {
                "layer": "S",
                "title": "search/index.py records every query it serves",
                "body": "Calls audit.record on each query. Nothing is read back.",
                "derives_from": ["A·01"],
                "emits_into": [audit_spec],
            }
        ],
        slice_name="discovery",
    )
    _, m = call("GET", f"/projects/{P}/spine?layer=S")
    deps = store.load_manifest(P).dependency_graph(store.load_graph(P))
    print("\n  SLICE DEPENDENCIES after the emission")
    for name, d in deps.items():
        print(f"    {name:12} -> {', '.join(d) or '(nothing)'}")
    print("    discovery emits into audit and still depends on nothing. An")
    print("    emission is not a dependency, which is the whole point of it.")

    print("\n  And the other direction is refused outright:")
    amend(
        P,
        audit_req,
        [
            {
                "layer": "S",
                "title": "search/index.py asks the audit log a question",
                "derives_from": ["A·01"],
                "depends_on": [audit_spec],
            }
        ],
        slice_name="discovery",
    )

    _, wp_cc = call("GET", f"/projects/{P}/work-package?slice=payouts")
    print("\n  payouts never mentioned audit. Its work package carries it anyway:")
    show("cross_cutting", [e["id"] + " " + e["slice"] for e in wp_cc["cross_cutting"]])

    # --------------------------------------------------------------- 5c
    step("5c.", "WAVES — the order, computed rather than chosen")
    _, w = call("GET", f"/projects/{P}/waves")
    for row in w["waves"]:
        print(
            f"    wave {row['wave']}  width {row['width']}  "
            f"{', '.join(row['slices'])}"
        )
    print("\n  audit is in wave 0 and nothing put it there. The edge rules")
    print("  leave a concern no outbound dependency to have, so it has no path")
    print("  to anything -- wave 0 falls out rather than being arranged.")
    print("\n  Slices in one wave have no edge between them, structurally, so")
    print("  agents working a wave never need to talk to each other and there")
    print("  is nothing to lock. Every earlier wave is finished first, so each")
    print("  one reads its dependencies as frozen artifacts.")
    if w["chain"]:
        print("\n  chain=true: every wave is one slice wide, so nothing can be")
        print("  done in parallel. A signal that the slices are too coupled.")

    # --------------------------------------------------------------- 5d
    step("5d.", "ISSUES — one part of the pipeline asking another for a fix")
    print("  The payouts agent finds things missing from the index. It does")
    print("  not message the discovery agent -- that agent is finished and")
    print("  gone. It files issues against the artifact and proceeds on a")
    print("  stated assumption.\n")
    for target, kind, claim, by in (
        ("S·01", "additive", "no way to ask the index for its size", "payouts"),
        ("S·01", "additive", "no way to page through results", "payouts"),
        ("S·01", "semantic", "the index returns ids where it said names",
         "discovery"),
    ):
        status, iss = call(
            "POST",
            f"/projects/{P}/issues",
            {
                "target": target,
                "kind": kind,
                "claim": claim,
                "raised_by": by,
                "assumption": "assumed the current shape and carried on",
            },
        )
        print(f"  raise_issue({kind:8}) -> {status} {iss['id']} against "
              f"{target} in {iss['target_slice']}")

    _, rec = call("GET", f"/projects/{P}/reconcile")
    print("\n  RECONCILIATION — grouped by target slice, in dependency order")
    for b in rec["batches"]:
        print(f"    {b['slice']} (wave {b['wave']}) — {len(b['issues'])} issue(s)")
        for i in b["issues"]:
            print(f"        [{i['kind']:8}] {i['claim']}")
        print(f"        re-run: {b['rerun'] or '(nothing)'}")
    for e in rec["escalations"]:
        print(f"    ESCALATED {e['issue']}: {e['reason']}")

    print("\n  The router read only those headers -- about thirty tokens each,")
    print("  never an assumption and never a target's body. That is what keeps")
    print("  its cost flat: a hundred issues across six slices is six batches,")
    print("  and the expensive reading happens in a session that was going to")
    print("  load that column anyway.")
    print("\n  The additive ones re-run nothing: a new entry changes no")
    print("  existing meaning. The semantic one names exactly what consumed")
    print("  the old meaning -- from the entries' own edges, not a whole column.")

    print("\n  And an issue that is not a repair goes to a human instead:")
    call(
        "POST",
        f"/projects/{P}/issues",
        {
            "target": "S·01",
            "kind": "semantic",
            "claim": "the index means something else entirely",
            "raised_by": "payouts",
        },
    )
    _, rec2 = call("GET", f"/projects/{P}/reconcile")
    for e in rec2["escalations"]:
        print(f"    ESCALATED {e['issue']} [{e['reason']}]")
        print(f"      {e['detail']}")

    # --------------------------------------------------------------- 5e
    step("5e.", "SPEC TO CODE — the diff a planner acts on, and the audit")
    print("  Code is not an entry in this graph. Modules declare which spec")
    print("  entries they implement, and that backlink is the only thing")
    print("  tying a file to the reasoning that produced it.\n")
    for path, implements in (
        ("search/index.py", ["S·01"]),
        ("search/filters.py", ["S·02"]),
        ("payouts/release.py", ["S·03"]),
    ):
        status, m = call(
            "POST", f"/projects/{P}/modules",
            {"path": path, "implements": implements},
        )
        print(f"  declare_module -> {status} {path} implements {m['implements']}")

    _, mods = call("GET", f"/projects/{P}/modules")
    show("spec entries no module claims", mods["unimplemented"])

    status, pl = call("GET", f"/projects/{P}/plan")
    print(f"\n  get_plan (first iteration) -> {status} issued={pl['issued']}")
    if pl["issued"]:
        show("diff.added", pl["diff"]["added"])
        print("\n  WRITE SET (editable)")
        for r in pl["write_set"]:
            print(f"    {r['path']:22} implements {r['implements']} [{r['slice']}]")
        print("  READ SET (context only)")
        for r in pl["read_set"]:
            print(f"    {r['path']:22} implements {r['implements']} [{r['slice']}]")
        if not pl["read_set"]:
            print("    (empty: on a first iteration every entry is in the diff,")
            print("     so there is no unchanged consumer to read. It fills up")
            print("     on the next plan, when only part of the spec has moved.)")
        print(f"\n  mark to pass back next time: {pl['mark']}")
        print("\n  Note this is a SPEC diff, never a git diff. Feeding a git")
        print("  diff to the planner makes code the source of truth: it starts")
        print("  reasoning about what the code does rather than what the spec")
        print("  says it should, and the spec layer is decorative in a few cycles.")

        print("\n  Afterwards, git diff DOES belong -- as audit, not input.")
        print("  The caller runs git and passes the paths; this unit never")
        print("  executes anything.")
        _, aud = call(
            "POST", f"/projects/{P}/audit",
            {
                "touched": pl["audit"]["editable_paths"] + ["search/sneaky.py"],
                "editable_paths": pl["audit"]["editable_paths"],
            },
        )
        show("clean", aud["clean"])
        show("undeclared", aud["undeclared"])
        print(f"    {aud['detail']}")

    # ---------------------------------------------------------------- 6
    step("6.", "REVIEW THE FINAL LAYER")
    _, review = call("GET", f"/projects/{P}/review?layer=A&slice=discovery")
    for row in review["entries"]:
        print(f"    {row['id']}  {row['title']}")
        for parent in row["derives_from_titles"]:
            print(f"        serves    {parent['id']} {parent['title']}")
        print(f"        served by {', '.join(row['served_by']) or '(nothing yet)'}")
        for c in row["comments"]:
            print(f"        comment   [{c['origin']}] {c['title']}")

    # ---------------------------------------------------------------- 7
    step("7.", "CHANGE — a correction, and how deep it is allowed to reach")
    print("  The buyer says ranking is wrong. First: what happens if someone")
    print("  tries to fix it by editing the spec directly?\n")
    fix = request("correction", "search ranking is wrong", project=P, origin="andrey")
    amend(
        P,
        fix,
        [
            {
                "layer": "S",
                "title": "just sort the results differently",
                "derives_from": ["A·01"],
                "supersedes": "S·01",
            }
        ],
        slice_name="discovery",
    )
    print("\n  Refused. Patching the spec while intent, behaviour and")
    print("  architecture still say the old thing is how artifacts start")
    print("  lying. The defect has to be classified upward first.\n")

    fix2 = request("correction", "search ranking is wrong", project=P, origin="andrey")
    result = amend(
        P,
        fix2,
        [
            {
                "layer": "B",
                "title": "A buyer can search sellers by keyword, ranked by rating",
                "body": "Relevance is keyword match, tie-broken by seller rating.",
                "derives_from": ["I·01"],
                "supersedes": "B·01",
            }
        ],
        slice_name="discovery",
    )
    show("blast radius (must be re-derived)", result.get("blast_radius"))
    gates(P)
    print("\n  A·01 now points at a retired entry, so the graph is UNSOUND.")
    print("  The stale reference is visible instead of silently rotting.")

    status, wp2 = call("GET", f"/projects/{P}/work-package?slice=discovery")
    print(f"\n  work_package(discovery) -> {status} issued={wp2['issued']}")
    print(f"    reason: {wp2.get('reason')}")
    print("\n  No code can be produced from a broken chain. This is the whole")
    print("  claim of the system, enforced mechanically rather than remembered.")

    status, wp3 = call("GET", f"/projects/{P}/work-package?slice=payouts")
    print(f"\n  work_package(payouts)   -> {status} issued={wp3['issued']}")
    print("    payouts is untouched by the correction, but soundness is a")
    print("    property of the whole graph, so it is held back too. Open"
          " question.")

    # ---------------------------------------------------------------- 8
    step("8.", "SPLIT A SLICE — membership moves, identifiers never do")
    before = store.load_manifest(P)
    show("discovery members before", sorted(str(m) for m in before.slices["discovery"].members))
    from mu_spec.identifiers import parse as pid

    store.split_slice(P, "discovery", "filtering", {pid("B·02"), pid("A·02"), pid("S·02")})
    after_m = store.load_manifest(P)
    show("discovery members after ", sorted(str(m) for m in after_m.slices["discovery"].members))
    show("filtering members       ", sorted(str(m) for m in after_m.slices["filtering"].members))
    print("\n  Every identifier is unchanged. discovery's membership is now")
    print("  non-contiguous, which is why it is a set and never a range.")

    # ---------------------------------------------------------------- 9
    # ---------------------------------------------------------------- 9
    step("8c.", "WHAT CAN BE LEARNED — reported, never enforced")
    print("  Nothing in this step gates anything. Every existing gate blocks")
    print("  on something definitionally broken; everything here is a proxy")
    print("  for a question nobody can answer yet, and baking a proxy into")
    print("  the one place the system is certain would be the worst trade in")
    print("  the design.\n")

    _, ins = call("GET", f"/projects/{P}/insights")
    cl = ins["change_locality"]
    print("  CHANGE LOCALITY — the primary score, and not a proxy")
    print(f"    {cl['changes']} changes · {cl['single_slice']} landed in one slice "
          f"· mean {cl['mean']}")
    for row in cl["worst"]:
        print(f"    {row['message']}  [{row['type']:10}] touched "
              f"{row['count']}: {', '.join(row['slices'])}")
    print("\n    A good slicing is one where a typical change lands inside a")
    print("    single slice. That is the definition, not a stand-in for it --")
    print("    and every request already recorded which entries it produced.")

    print("\n  CORRECTIONS — where defects entered (DESIGN.md §9)")
    print(f"    {ins['corrections']['by_layer'] or '(none yet)'}")
    print("    Clustered at intent means the interview was too shallow to")
    print("    derive from. Clustered lower means the prompting is weak.")
    print("    Once a fix has propagated the graph just looks correct, so")
    print("    this has to be recorded at the moment it happens.")

    print("\n  STRUCTURE")
    for row in ins["slices"]:
        print(f"    {row['slice']:11} size {row['size']}  in {row['internal_edges']}"
              f"  out {row['outbound_edges']}  emits {row['emissions']}"
              f"  cohesion {row['cohesion']}")

    print("\n  TRIAL A DIFFERENT CUT — scored, and nothing created")
    _m = store.load_manifest(P)
    merged = sorted(
        str(i)
        for name in ("discovery", "filtering", "payouts")
        for i in _m.slices[name].members
    )
    status, sc = call(
        "POST",
        f"/projects/{P}/slicing/score",
        {
            "proposal": {
                "product": merged,
                "audit": sorted(str(i) for i in _m.slices["audit"].members),
            },
            "types": {"audit": "cross_cutting"},
        },
    )
    print("    what if discovery, filtering and payouts were one slice?")
    print(f"    score -> {status} legal={sc.get('legal', sc.get('error'))}")
    for row in sc["slices"]:
        print(f"      {row['slice']:9} size {row['size']}  out "
              f"{row['outbound_edges']}  cohesion {row['cohesion']}")
    for w in sc["warnings"]:
        print(f"      warning: {w}")
    print("    payouts' one outbound edge became internal — the cut that")
    print("    contains it scores better on coupling, and costs more in size.")
    after = store.load_manifest(P)
    print(f"    slices actually on disk, unchanged: {sorted(after.slices)}")
    print("\n    Slices split and never merge, so a ratified cut is expensive")
    print("    to undo. Scoring moves the argument to where the remedy is free.")

    print("\n  THE LIFECYCLE — what the graph cannot tell you")
    _, ev = call("GET", f"/projects/{P}/events")
    for e in ev["events"][:12]:
        detail = ""
        if e["kind"] == "correction":
            detail = f"entered at {e['facts'].get('entered_at')}"
        elif e["kind"] == "refusal":
            detail = e["facts"].get("reason", "")
        elif e["kind"] == "request":
            detail = f"{e['facts'].get('type')}: {e['facts'].get('title','')[:38]}"
        elif e["kind"] == "issue_raised":
            detail = f"assumed: {e['facts'].get('assumption','')[:34]}"
        elif e["kind"] == "derivation":
            detail = ",".join(e["refs"][:4])
        print(f"    {e['seq']:>3}  {e['kind']:14} {detail}")
    if len(ev["events"]) > 12:
        print(f"    … {len(ev['events']) - 12} more")
    print("\n    A gate that failed and was then fixed leaves no trace in the")
    print("    fixed graph. A correction's layer of origin disappears once it")
    print("    has propagated. What an agent could not derive is nowhere in")
    print("    the state at all. That is what this log is for.")

    # ---------------------------------------------------------------- 9
    step("9.", "WHAT IS ON DISK")
    for path in sorted(root.rglob("*")):
        if path.is_file():
            print(f"    {path.relative_to(root)}  ({path.stat().st_size} bytes)")
    sample = root / P / "spec" / "discovery.jsonl"
    if sample.exists():
        print(f"\n  ---- {sample.relative_to(root)} ----")
        print("".join(f"  | {line}" for line in sample.read_text(encoding="utf-8").splitlines(True)))

    step("10.", "THE REQUEST LOG")
    _, all_msgs = call("GET", "/inbox")
    for m in all_msgs["messages"]:
        produced = (m.get("resolution") or {}).get("produced", [])
        print(f"    {m['id']}  {m['type']:11} {m['status']:9} {m['title'][:44]}")
        if produced:
            print(f"              produced: {', '.join(produced)}")
    print("\n  Every entry in the graph traces back through an amendment to a")
    print("  request, and out to the person who asked for it.")

    print(f"\nstorage left at: {root.resolve()}")
    if server is not None:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
