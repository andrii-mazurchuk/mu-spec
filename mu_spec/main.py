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

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9006


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)

    print(f"mu-spec serving on http://{args.host}:{args.port}")
    serve(args.host, args.port, Path(args.prompts_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
