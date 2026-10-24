"""A project's comparable numbers, from an exported snapshot (V3).

Two kinds of number, by where they can come from:

- **mu-spec's own** (`METRICS`): computed here from the graph, manifest,
  requests and issues, through `mu_spec.metrics` -- the same functions behind
  GET /insights. A snapshot is replayed with the V1 loader; a live project is
  read from /insights and /spine (`summarise` takes either).
- **forge's ledger** (`LEDGER`): stops, rulings, denials, attempts, idle time,
  cost. mu-spec never sees these; they are supplied as a file and passed
  through, normalised per unit so projects of different size compare.

Every number reports. None gates, and no threshold here fails anything.

    python -m replay.baseline <snapshot_dir> [--ledger metrics.json] [--out file.json]
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

from mu_spec import waves
from mu_spec.identifiers import LAYER_NAMES, SPEC, TEST, InvalidIdentifier, parse
from mu_spec.inbox import Message
from mu_spec.metrics import change_locality, corrections_by_layer, structural
from replay import harness

# name -> (which way is better, what it means). "" = context, no direction:
# a bigger project is not a worse one.
METRICS = {
    "live_entries": ("", "live entries, all layers"),
    "live_spec": ("", "live spec entries"),
    "live_test": ("", "live test entries"),
    "slices": ("", "slices"),
    "wave_depth": ("", "waves in the schedule"),
    "supersessions": ("lower", "entries ever retired by a successor (snapshot only)"),
    "supersessions_per_live_entry": ("lower", "supersessions / live entries"),
    "changes": ("", "requests that produced sliced entries"),
    "change_locality_mean": ("lower", "slices a change touched, mean; 1 is perfect"),
    "single_slice_share": ("higher", "share of changes inside one slice"),
    "corrections": ("lower", "correction requests that produced entries"),
    **{f"corrections_at_{name}": ("", f"corrections entering at {name}")
       for name in LAYER_NAMES.values()},
    "cohesion_weighted": ("higher", "dependency edges staying in their slice, all slices"),
    "issues": ("lower", "issues raised"),
    "issues_semantic": ("lower", "semantic issues raised"),
    "issues_per_live_spec": ("lower", "issues / live spec entries"),
}

LEDGER = {
    "units_touched": ("", "units forge worked"),
    "stops_per_unit": ("lower", "implementer stops / unit"),
    "spec_fault_rulings_per_unit": ("lower", "CTO spec-fault rulings / unit"),
    "spec_fault_rulings_without_issue": ("lower", "spec-fault rulings that filed no issue"),
    "denials_per_unit": ("lower", "permission denials / unit"),
    "first_attempt_share": ("higher", "units done in one attempt"),
    "follow_ups_per_unit": ("lower", "follow-up tickets / unit"),
    "idle_gaps_over_1h": ("lower", "driver idle gaps over 1 h"),
    "idle_hours_over_1h": ("lower", "hours in those gaps"),
    "cost_usd_per_unit": ("lower", "role-run cost / unit"),
    "lead_time_p50_h": ("lower", "median admitted -> done, hours"),
}


def _ratio(a, b, digits=3):
    return round(a / b, digits) if a is not None and b else None


def summarise(insights: dict, spine: list[dict], supersessions: int | None = None) -> dict:
    """The comparable numbers from a GET /insights body and a spine.

    `supersessions` is None over HTTP: history is never served, so only a
    snapshot can count it."""
    live = []
    for row in spine:
        try:
            live.append(parse(str(row["id"])).layer)
        except (InvalidIdentifier, KeyError):
            continue
    locality = insights.get("change_locality") or {}
    corrections = insights.get("corrections") or {}
    slices = insights.get("slices") or []
    issues = insights.get("issues") or {}
    internal = sum(s.get("internal_edges", 0) for s in slices)
    edges = internal + sum(s.get("outbound_edges", 0) for s in slices)
    by_layer = corrections.get("by_layer") or {}
    out = {
        "live_entries": len(live),
        "live_spec": live.count(SPEC),
        "live_test": live.count(TEST),
        "slices": len(slices),
        "wave_depth": len(insights.get("waves") or ()),
        "supersessions": supersessions,
        "supersessions_per_live_entry": _ratio(supersessions, len(live)),
        "changes": locality.get("changes"),
        "change_locality_mean": locality.get("mean"),
        "single_slice_share": _ratio(locality.get("single_slice"), locality.get("changes")),
        "corrections": corrections.get("corrections"),
        **{f"corrections_at_{n}": by_layer.get(n, 0) for n in LAYER_NAMES.values()},
        "cohesion_weighted": _ratio(internal, edges, 2),
        "issues": issues.get("total"),
        "issues_semantic": issues.get("semantic"),
        "issues_per_live_spec": _ratio(issues.get("total"), live.count(SPEC)),
    }
    return out


class _Messages:
    """The two `Inbox.list` filters the metrics use, over an exported list."""

    def __init__(self, messages: list[Message]):
        self._messages = messages

    def list(self, project=None, kind=None, **_):
        return [m for m in self._messages
                if (not project or m.project == project) and (not kind or m.type == kind)]


def _rows(path: Path, key: str) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get(key, []) if isinstance(data, dict) else data
    return list({r["id"]: r for r in rows}.values())  # last line for an id wins


def _at(history: harness.History, t: float, messages: list[Message], issues: list[dict]) -> dict:
    graph, manifest = harness.build(history, t)
    inbox = _Messages([m for m in messages if m.at <= t])
    seen = [i for i in issues if i.get("at", 0) <= t]
    insights = {
        "change_locality": change_locality(manifest, inbox, history.project),
        "corrections": corrections_by_layer(graph, inbox, history.project),
        **structural(manifest, graph),
        "issues": {"total": len(seen), "semantic": sum(i.get("kind") == "semantic" for i in seen)},
        "waves": waves.schedule(manifest, graph).waves,
    }
    return summarise(insights, graph.spine(), len(graph.superseded()))


def _disagreements(root: Path, final: dict) -> list[str]:
    """The replay's final numbers against the node's own exported insights,
    on every field both hold. Empty is the reconstruction being right."""
    path = root / "insights.json"
    if not path.exists():
        return []
    node = summarise(json.loads(path.read_text(encoding="utf-8")), [])
    return [f"{k}: node {v} != replay {final[k]}" for k, v in node.items()
            if k not in _SPINE_ONLY and v != final[k]]


# Fields an insights body alone cannot give: they need the spine or history.
_SPINE_ONLY = ("live_entries", "live_spec", "live_test", "supersessions",
               "supersessions_per_live_entry", "issues_per_live_spec")


def compute(snapshot: Path | str) -> dict:
    """The final state's numbers, and each cut's on what existed when it was taken."""
    history = harness.load(snapshot)
    root = Path(snapshot)
    messages = [Message.from_json(r) for r in _rows(root / "inbox.json", "messages")]
    issues = _rows(root / "issues.json", "issues")
    last = max((e["at"] for e in history.events), default=0.0)
    final = _at(history, float("inf"), messages, issues)
    return {
        "provenance": {
            "project": history.project,
            "snapshot": str(root),
            "snapshot_last_event": datetime.datetime.fromtimestamp(
                last, datetime.timezone.utc).isoformat(timespec="seconds"),
            "disagrees_with_node_insights": _disagreements(root, final),
        },
        "metrics": final,
        "per_cut": [
            {"cut": c["seq"], "at": c["at"], "metrics": _at(history, c["at"], messages, issues)}
            for c in history.cuts
        ],
    }


def ledger(raw: dict) -> dict:
    """forge's numbers in `LEDGER` names. A retro `index/metrics.json` is
    normalised per unit; anything else is taken as already in these names."""
    if "units_touched" not in raw or "events" not in raw:
        return dict(raw)
    units = raw["units_touched"]
    attempts = raw.get("max_attempt_per_unit") or {}
    gaps = raw.get("driver_idle_gaps_over_1h") or []
    return {
        "units_touched": units,
        "stops_per_unit": _ratio(raw["events"].get("stopped", 0), units),
        "spec_fault_rulings_per_unit": _ratio((raw.get("rulings") or {}).get("spec_fault"), units),
        "spec_fault_rulings_without_issue": raw.get("rulings_spec_fault_without_issue"),
        "denials_per_unit": _ratio(sum((raw.get("permission_denials_by_tool") or {}).values()), units),
        "first_attempt_share": _ratio(attempts.get("1"), sum(attempts.values())),
        # Not in the DARK retro's index; forge supplies it when it counts it.
        "follow_ups_per_unit": raw.get("follow_ups_per_unit"),
        "idle_gaps_over_1h": len(gaps),
        "idle_hours_over_1h": round(sum(g["hours"] for g in gaps), 1),
        "cost_usd_per_unit": _ratio(raw.get("cost_usd_total"), units, 2),
        "lead_time_p50_h": (raw.get("lead_time_h_admitted_to_done") or {}).get("p50"),
    }


def _sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.baseline", description=__doc__.split("\n")[0])
    parser.add_argument("snapshot_dir")
    parser.add_argument("--ledger", help="forge's ledger: a retro index/metrics.json or a flat LEDGER file")
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args(argv)
    try:
        result = compute(args.snapshot_dir)
    except harness.SnapshotError as exc:
        print(f"error: {exc}")
        return 2
    result["provenance"]["code_sha"] = _sha()
    result["provenance"]["computed_on"] = datetime.date.today().isoformat()
    if args.ledger:
        result["ledger"] = ledger(json.loads(Path(args.ledger).read_text(encoding="utf-8")))
        result["provenance"]["ledger"] = args.ledger
    cuts = result.pop("per_cut")
    text = json.dumps(result, indent=1, ensure_ascii=False)[:-2] + ',\n "per_cut": [\n  ' + \
        ",\n  ".join(json.dumps(c, ensure_ascii=False) for c in cuts) + "\n ]\n}"  # a line a cut
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
