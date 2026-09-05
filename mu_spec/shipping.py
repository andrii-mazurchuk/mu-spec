"""Shipping lifecycle events to whichever unit holds the logs role.

Push, one entry per call, best-effort. The receiving contract is
`POST /entries` with `{"source_unit", "entry_type", "payload"}` -- the id and
timestamp are assigned there, never here.

Three things this module is careful about, and each is a rule from the
standard rather than a preference:

**The peer is addressed by role, never by name.** The logs unit is resolved
at call time from the delivery policy the gateway writes into this
directory. Hardcoding a unit name makes that unit un-renameable and
un-swappable, which is the coupling the whole system is arranged to avoid.

**Everything degrades.** A missing policy file, an unreachable peer, a
refused entry type, a malformed response -- all resolve to "not shipped" and
nothing else. Logging is never the point of the call that triggered it, and
an amendment that succeeded must never be reported as failed because its
audit line did not land. There is no retry and no queue: the local lifecycle
log is the durable record, and this is a copy for whoever aggregates.

**The entry type is new, and may be refused for a while.** `project_event`
is not in the receiving unit's vocabulary yet, and that vocabulary is
enforced there rather than negotiated. Until it is added, every ship attempt
returns a refusal and the local log carries on regardless -- which is the
whole reason this is decoupled rather than awaited.

The transport is injected. Nothing here opens a socket during tests, and the
default sender is the only place `urllib` is touched.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

UNIT_NAME = "mu-spec"

# The domain-event type. Distinct from `session_run` on purpose: that
# describes one agent run, this describes something that happened to the
# specification and stays interesting for months.
ENTRY_TYPE = "project_event"

DELIVERY_POLICY = "delivery_policy.json"
PEERS = "peers.json"


def _read_json(path: Path, empty=dict):
    """A config file that is missing, unreadable or malformed is an empty
    value, never an exception. Absence is normal throughout this system.

    Both shapes are accepted for the peer list because the gateway writes it
    "flat and unfiltered" and this unit should not care whether that means a
    list or a mapping keyed by name.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty()
    return raw if isinstance(raw, (dict, list)) else empty()


def resolve_logs_url(root: Path) -> str | None:
    """Find the logs peer by role.

    The delivery policy names the role's target; peers.json carries that
    unit's base URL. Either being absent means nothing is shipped, which is
    the correct behaviour for a unit running without a gateway around it --
    including every test and the walkthrough.
    """
    policy = _read_json(Path(root) / DELIVERY_POLICY)
    target = policy.get("logs")
    if isinstance(target, dict):
        target = target.get("unit") or target.get("name")
    if not isinstance(target, str) or not target:
        return None

    peers = _read_json(Path(root) / PEERS)
    if isinstance(peers, dict):
        peers = peers.get("units", peers)
    entries = list(peers.values()) if isinstance(peers, dict) else peers
    if not isinstance(entries, list):
        return None
    for peer in entries:
        if isinstance(peer, dict) and peer.get("name") == target:
            base = peer.get("base_url")
            return base.rstrip("/") if isinstance(base, str) and base else None
    return None


def _post(url: str, body: dict, timeout: float = 2.0) -> bool:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def ship(
    root: Path,
    event: Any,
    sender: Callable[[str, dict], bool] = None,
) -> bool:
    """Copy one lifecycle event to the logs unit. Returns whether it landed.

    The return value is for the caller's own bookkeeping only. Nothing in
    this unit changes behaviour because a log line did not ship.
    """
    base = resolve_logs_url(root)
    if base is None:
        return False
    body = {
        "source_unit": UNIT_NAME,
        "entry_type": ENTRY_TYPE,
        "payload": event.to_json() if hasattr(event, "to_json") else dict(event),
    }
    send = sender or _post
    try:
        return bool(send(f"{base}/entries", body))
    except Exception:
        # A sender that raises is still just a failed ship. This is the
        # outermost degradation boundary -- past here, nothing may escape
        # into the request handler that triggered the write.
        return False
