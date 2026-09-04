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
        f"  {'id':7} {'layer':13} {'slice':12} {'derives from':13} "
        f"{'depends on':11} title"
    )
    print(f"  {'-' * 80}")
    for row in payload["spine"]:
        print(
            f"  {row['id']:7} {row['id'][0]:13} {str(row['slice'] or '-'):12} "
            f"{','.join(row['derives_from']) or '-':13} "
            f"{','.join(row['depends_on']) or '-':11} {row['title']}"
        )


def gates(project: str) -> dict:
    _, payload = call("GET", f"/projects/{project}/gates")
    state = "SOUND" if payload["sound"] else "UNSOUND"
    state += " / COMPLETE" if payload["complete"] else " / incomplete"
    print(f"\n  GATES: {state}")
    for f in payload["findings"]:
        print(f"    - {f['kind']:9} {f['id']:7} {f['detail']}")
    if not payload["findings"]:
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
