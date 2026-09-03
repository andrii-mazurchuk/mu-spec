"""End-to-end walkthrough of the pipeline, against a real HTTP server.

Run it and read the output top to bottom:

    python walkthrough.py            # temporary project, cleaned up by the OS
    python walkthrough.py --keep DIR # leave the files behind to inspect

Every step is a real HTTP request to a real server on a real socket. Nothing
is stubbed. The point is to make the pipeline legible: you should be able to
watch knowledge enter as intent and arrive as a bounded work package, and
see the gates refuse the things they are supposed to refuse.
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


def call(method: str, path: str, body: dict | None = None):
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
    print(f"  {'id':7} {'layer':13} {'slice':12} {'derives from':14} title")
    print(f"  {'-' * 68}")
    for row in payload["spine"]:
        print(
            f"  {row['id']:7} {row['id'][0]:13} {str(row['slice'] or '-'):12} "
            f"{','.join(row['derives_from']) or '-':14} {row['title']}"
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


def amend(project: str, slice_name: str, entries: list[dict]) -> dict:
    status, payload = call(
        "POST",
        f"/projects/{project}/amendments",
        {"slice": slice_name, "entries": entries},
    )
    verb = "ADMITTED" if payload.get("admitted") else "REFUSED"
    print(f"  submit_amendment({slice_name}) -> {status} {verb} {payload.get('created', '')}")
    for f in payload.get("findings", []):
        print(f"    refused because: {f['kind']} {f['id']} -- {f['detail']}")
    return payload


def main(argv=None) -> int:
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", default=None, help="write files here and leave them")
    parser.add_argument("--port", type=int, default=9107)
    args = parser.parse_args(argv)

    root = Path(args.keep) if args.keep else Path(tempfile.mkdtemp()) / "projects"
    root.mkdir(parents=True, exist_ok=True)
    store = ProjectStore(root)

    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), build_handler(store, Path("prompts"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{args.port}"
    print(f"server up on {BASE}, storage at {root.resolve()}")

    P = "marketplace"

    # ---------------------------------------------------------------- 1
    step("1.", "INITIATE — a human states intent, and stops there")
    status, payload = call(
        "POST",
        "/projects",
        {
            "project": P,
            "intent": [
                {
                    "title": "Buyers can find the right seller quickly",
                    "body": "Buyers abandon the marketplace when search is slow "
                    "or returns irrelevant sellers.",
                },
                {
                    "title": "Sellers are paid within 24 hours of delivery",
                    "body": "Late payment is the top reason sellers leave.",
                },
            ],
        },
    )
    show("status", status)
    show("created", payload["created"])
    gates(P)
    print("\n  Both intent entries are unserved: stated, nothing built. That is")
    print("  the pipeline's to-do list, computed rather than tracked by hand.")

    # ---------------------------------------------------------------- 2
    step("2.", "PROPAGATE — behaviour derived from intent, by slice")
    amend(
        P,
        "discovery",
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
    )
    amend(
        P,
        "payouts",
        [
            {
                "layer": "B",
                "title": "A seller is paid once delivery is confirmed",
                "body": "Payment is released within 24h of confirmation. "
                "A disputed delivery holds payment.",
                "derives_from": ["I·02"],
            }
        ],
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
        "discovery",
        [
            {"layer": "A", "title": "legitimate", "derives_from": ["B·01"]},
            {"layer": "A", "title": "derives from nothing real", "derives_from": ["B·99"]},
        ],
    )
    _, after = call("GET", f"/projects/{P}/spine?layer=A")
    show("architecture entries after the refusal", [r["id"] for r in after["spine"]])

    # ---------------------------------------------------------------- 4
    step("4.", "PROPAGATE — architecture, then spec")
    amend(
        P,
        "discovery",
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
    )
    amend(
        P,
        "payouts",
        [
            {
                "layer": "A",
                "title": "Payouts are driven by a delivery-confirmed event",
                "body": "Polling would breach the 24h window at scale.",
                "derives_from": ["B·03"],
            }
        ],
    )
    amend(
        P,
        "discovery",
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
    )
    amend(
        P,
        "payouts",
        [
            {
                "layer": "S",
                "title": "payouts/release.py releases a payment on confirmation",
                "body": "Module payouts/release.py. on_delivery_confirmed(order) -> "
                "Payment | None. Returns None while a dispute is open.",
                "derives_from": ["A·03"],
            }
        ],
    )
    spine(P, "fully propagated")
    gates(P)

    # ---------------------------------------------------------------- 5
    step("5.", "RETRIEVE THE FINAL LAYER — the coding agent's bounded context")
    call(
        "POST",
        f"/projects/{P}/comments",
        {
            "target": "A·01",
            "author": "andrey",
            "body": "Is an inverted index overkill before we have 10k sellers?",
        },
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
    print("\n  READ SET (declared dependencies -- context, not editable)")
    print(f"    {wp['read_set'] or '(none: discovery declares no dependencies)'}")
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
            print(f"        comment   [{c['author']}] {c['body']}")

    # ---------------------------------------------------------------- 7
    step("7.", "CHANGE — a defect, classified to its layer")
    print("  The buyer says ranking is wrong. That is not a code bug: the")
    print("  behaviour entry never said what 'relevant' means. Fix it at B.\n")
    status, defect = call(
        "POST",
        f"/projects/{P}/defects",
        {
            "layer": "B",
            "title": "A buyer can search sellers by keyword, ranked by rating",
            "body": "Relevance is keyword match, tie-broken by seller rating.",
            "supersedes": "B·01",
        },
    )
    show("new entry", defect["id"])
    show("supersedes", defect["supersedes"])
    show("blast radius (must be re-derived)", defect["blast_radius"])
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
    print("    payouts is untouched by the defect, but soundness is a property")
    print("    of the whole graph, so it is held back too. Worth questioning.")

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
    sample = root / P / "spec" / "discovery.md"
    if sample.exists():
        print(f"\n  ---- {sample.relative_to(root)} ----")
        print("".join(f"  | {line}" for line in sample.read_text(encoding="utf-8").splitlines(True)))

    print(f"\nstorage left at: {root.resolve()}")
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
