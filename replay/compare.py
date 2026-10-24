"""Side by side: a baseline against a current project, report-only (V3).

    python -m replay.compare <baseline.json> <current> [--ledger forge.json]

`current` is a file `replay.baseline` wrote, a snapshot directory (computed
on the spot), or a live project's URL, `http://host:port/projects/<name>`,
read through GET /insights and /spine. `--ledger` supplies forge's numbers
for the current side. A number missing on either side prints `n/a`. The exit
code is 0 whatever the numbers say: this reports, it never gates.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from replay import baseline

HEAD = ("metric", "baseline", "current", "delta", "better", "")


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    return format(value, "g") if isinstance(value, float) else str(value)


def _row(name: str, better: str, b, c) -> tuple:
    if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
        return (name, _fmt(b), _fmt(c), "n/a", better, "")
    delta = round(c - b, 3)
    verdict = ""
    if better and delta:
        verdict = "better" if (delta < 0) == (better == "lower") else "worse"
    return (name, _fmt(b), _fmt(c), format(delta, "+g"), better, verdict)


def rows(base: dict, current: dict) -> list[tuple]:
    out = []
    for section, spec in (("metrics", baseline.METRICS), ("ledger", baseline.LEDGER)):
        b, c = base.get(section) or {}, current.get(section) or {}
        out += [_row(name, better, b.get(name), c.get(name)) for name, (better, _) in spec.items()]
    return out


def render(base: dict, current: dict) -> str:
    table = rows(base, current)
    split = len(baseline.METRICS)
    widths = [max(len(r[i]) for r in table + [HEAD]) for i in range(len(HEAD))]
    line = lambda r: "  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip()
    out = [line(HEAD), "-- mu-spec (computed) --"]
    out += [line(r) for r in table[:split]]
    out += ["-- forge ledger (supplied) --"] + [line(r) for r in table[split:]]
    return "\n".join(out)


def _get(url: str):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_current(arg: str) -> dict:
    if arg.startswith(("http://", "https://")):
        url = arg.rstrip("/")
        return {"metrics": baseline.summarise(_get(url + "/insights"), _get(url + "/spine")["spine"])}
    path = Path(arg)
    if path.is_dir():
        return baseline.compute(path)
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.compare", description=__doc__.split("\n")[0])
    parser.add_argument("baseline")
    parser.add_argument("current")
    parser.add_argument("--ledger", help="forge's ledger for the current side")
    args = parser.parse_args(argv)
    try:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        current = load_current(args.current)
        if args.ledger:
            current["ledger"] = baseline.ledger(json.loads(Path(args.ledger).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as exc:  # unreadable input, not a bad number
        print(f"error: {exc}")
        return 2
    print(render(base, current))
    return 0


if __name__ == "__main__":
    sys.exit(main())
