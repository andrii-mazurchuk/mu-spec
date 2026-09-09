"""`mu-spec` entry point. The gateway launches this via the unit's
`start_cmd`; host and port come from the environment the gateway injects,
with defaults for running it by hand.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mu_spec.server import serve
from mu_spec.storage import ProjectStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9006

# The one directory this unit keeps private state under. The gateway
# injects HOLONIC_STATE_DIR and creates it before this process starts --
# see holonic-node/docs/UNIT_STANDARDS.md, "Private storage". The
# fallback is what lets mu-spec run standalone, with no gateway.
STATE_DIR_ENV = "HOLONIC_STATE_DIR"
DEFAULT_STATE_DIR = "state"
PROJECTS_SUBDIR = "projects"


def state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV, DEFAULT_STATE_DIR))


def default_root() -> Path:
    """Resolved at call time, not import time, so the environment the
    gateway injects is honoured however this module gets loaded. The
    graph is this unit's whole reason to exist and is append-only and
    identifier-stable -- writing it to the wrong directory is not a
    recoverable mistake, so the resolution order is pinned by tests."""
    return state_dir() / PROJECTS_SUBDIR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mu-spec")
    parser.add_argument("--host", default=os.environ.get("MU_SPEC_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MU_SPEC_PORT", DEFAULT_PORT))
    )
    parser.add_argument(
        "--prompts-dir",
        default=os.environ.get("MU_SPEC_PROMPTS_DIR", "prompts"),
        help="directory backing GET /prompts/<tier>",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="this unit's private storage -- where projects live "
        f"(default: <{STATE_DIR_ENV}>/{PROJECTS_SUBDIR}; MU_SPEC_ROOT "
        "still overrides it)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # MU_SPEC_ROOT predates the standard and still wins, so an existing
    # deployment pinning it keeps working untouched.
    root = Path(args.root or os.environ.get("MU_SPEC_ROOT") or default_root())

    print(f"mu-spec serving on http://{args.host}:{args.port} (root: {root})")
    store = ProjectStore(root)
    root.mkdir(parents=True, exist_ok=True)
    serve(args.host, args.port, store, Path(args.prompts_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
