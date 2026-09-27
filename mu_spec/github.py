"""The GitHub REST client: create an issue, declare what blocks it.

Stdlib `urllib.request`, because two POSTs do not earn a dependency and this
unit has none.

Everything touching the outside world is injected -- the opener, the sleep --
so the whole of this is testable without a socket and without waiting. That is
the same rule the rest of the unit follows for clocks and filesystem roots.

**One exception type leaves this module.** `GitHubError` carries the status and
whatever GitHub said. A caller that also has to know about `HTTPError`,
`URLError`, `JSONDecodeError` and `KeyError` is a caller that will miss the
next thing urllib decides to raise.

**The asymmetry worth stating once**, because getting it wrong fails several
hundred issues into an emission: creating an issue returns both an `id` (the
int64 database id) and a `number` (the `#123` a person sees), and they are
different values. The dependency PATH takes the number; the dependency BODY
takes the id. So keep the id of everything created, not just the number.

**There is no `blocking` endpoint and none is wanted.** "A blocks B" is
expressed by posting A's id to B's `blocked_by`, which is precisely what a
unit's `follows` already means. No inversion anywhere.

Not an interface, and not a base class. A second tracker becomes a second
module with its own two functions; an abstraction designed before its second
case exists would be shaped by this one and fit neither.
"""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.error
import urllib.request
from typing import Callable

API_BASE = "https://api.github.com"

# Pinned rather than defaulted. Without this header GitHub is free to move the
# default forward and change a field under a client that never asked it to.
API_VERSION = "2026-03-10"

# GitHub rejects requests without one.
USER_AGENT = "mu-spec"

# Statuses worth trying again. 429 is the explicit rate limit; 5xx is GitHub
# having a moment. 403 is conditional and handled separately -- it means both
# "slow down" and "you may not", and those want opposite responses.
_RETRY = frozenset({429, 500, 502, 503, 504})

# What a secondary rate limit says. GitHub returns 403 for it, and 403 for a
# token that lacks `Issues: write`; the message is the only thing that
# distinguishes them, and retrying the second one four times changes nothing,
# takes four times as long, and buries the real cause.
_RATE_LIMITED = ("secondary rate limit", "rate limit exceeded", "abuse detection")


class GitHubError(Exception):
    """Anything that stopped a call working, with the status if there was one."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclasses.dataclass(frozen=True)
class Issue:
    """What creating an issue gives back that is worth keeping.

    Both numbers, deliberately: see the module docstring.
    """

    id: int
    number: int
    url: str = ""


def _is_rate_limit(message: str) -> bool:
    low = message.lower()
    return any(phrase in low for phrase in _RATE_LIMITED)


class GitHub:
    """A token, and the two writes an emission needs."""

    def __init__(
        self,
        token: str,
        *,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        api_base: str = API_BASE,
        attempts: int = 4,
        pace: float = 0.0,
        timeout: float = 30.0,
    ) -> None:
        """`pace` is a wait before every write.

        The cheap half of not tripping a secondary rate limit: `dark` needs 358
        writes, and sending them as fast as the socket allows is exactly what
        that limit exists to stop. The expensive half is the backoff below,
        which only runs once it is already too late.
        """
        if not token:
            # Issues are readable anonymously, so without this the failure
            # would surface at the first write as a 401 about something else.
            raise ValueError(
                "a GitHub token is required to create issues; none was given"
            )
        self._token = token
        self._opener = opener
        self._sleep = sleep
        self._base = api_base.rstrip("/")
        self._attempts = max(1, attempts)
        self._pace = pace
        self._timeout = timeout

    # -- the two writes -----------------------------------------------------

    def create_issue(
        self,
        repo: str,
        title: str,
        body: str,
        labels: "list[str] | tuple[str, ...]" = (),
    ) -> Issue:
        """Create one issue. Returns its id AND its number.

        Note that labels, assignees and milestones are **silently dropped** by
        GitHub when the token lacks push access -- no error, no warning, and
        every issue simply arrives unlabelled. Nothing here can detect that;
        whoever emits should check its own token once rather than wonder later.
        """
        payload: dict = {"title": title, "body": body}
        if labels:
            # Omitted rather than sent empty: `[]` is a statement about labels,
            # and this client should not make one it was not asked to.
            payload["labels"] = list(labels)
        raw = self._post(f"/repos/{repo}/issues", payload)
        try:
            return Issue(
                id=int(raw["id"]),
                number=int(raw["number"]),
                url=str(raw.get("html_url", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Defaulting the id to 0 here would wire every dependency in the
            # emission to nothing, and report success doing it.
            raise GitHubError(
                f"a created issue came back without a usable id and number: {exc}",
                body=json.dumps(raw)[:400],
            ) from exc

    def add_blocked_by(self, repo: str, blocked_number: int, blocker_id: int) -> None:
        """Declare that `blocked_number` is blocked by the issue with
        `blocker_id`.

        Number in the path, id in the body. They are different values and
        GitHub accepts the wrong one silently in the sense that matters: it
        either 404s or wires an issue you did not mean.

        GitHub caps a relationship at 50 per issue. Nothing here enforces that
        -- the cap is GitHub's and a 422 says so -- and no measured unit comes
        close: the widest on `dark` follows 15.
        """
        self._post(
            f"/repos/{repo}/issues/{int(blocked_number)}/dependencies/blocked_by",
            {"issue_id": int(blocker_id)},
        )

    # -- the transport ------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self._base + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )
        return self._send(request)

    def _send(self, request) -> dict:
        last: GitHubError | None = None
        for attempt in range(self._attempts):
            if self._pace:
                self._sleep(self._pace)
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    return self._decode(response)
            except urllib.error.HTTPError as exc:
                message = self._message(exc)
                retryable = exc.code in _RETRY or (
                    exc.code == 403 and _is_rate_limit(message)
                )
                last = GitHubError(
                    f"GitHub answered {exc.code}: {message}",
                    status=exc.code,
                    body=message,
                )
                if not retryable or attempt == self._attempts - 1:
                    raise last from exc
                self._sleep(self._wait(exc, attempt))
            except urllib.error.URLError as exc:
                last = GitHubError(f"GitHub was unreachable: {exc.reason}")
                if attempt == self._attempts - 1:
                    raise last from exc
                self._sleep(self._wait(None, attempt))
        # Unreachable: the final attempt either returns or raises above.
        raise last or GitHubError("GitHub could not be reached")

    def _wait(self, exc, attempt: int) -> float:
        """How long before trying again.

        `Retry-After` is GitHub saying exactly how long; obeying it is both
        faster and the only way not to make a secondary limit worse. Failing
        that, back off geometrically from one second.
        """
        if exc is not None:
            named = (getattr(exc, "headers", None) or {}).get("Retry-After")
            if named:
                try:
                    return float(named)
                except (TypeError, ValueError):
                    pass
        return float(2**attempt)

    @staticmethod
    def _decode(response) -> dict:
        raw = response.read()
        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # A proxy or an error page. Saying "not JSON" and showing the start
            # of it beats a traceback about a token at position 1.
            raise GitHubError(
                f"GitHub's answer was not JSON: {raw[:120]!r}"
            ) from exc
        return decoded if isinstance(decoded, dict) else {"data": decoded}

    @staticmethod
    def _message(exc: urllib.error.HTTPError) -> str:
        """What GitHub actually objected to.

        The body carries it; `msg` is only the status phrase. A 422 on an
        oversized issue and a 422 on a bad label look identical without this.
        """
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001 -- a spent or absent stream
            raw = b""
        if not raw:
            return str(exc.msg or "")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", "replace")[:400]
        parts = [str(body.get("message", ""))]
        for error in body.get("errors", []) or []:
            if isinstance(error, dict):
                parts.append(
                    str(error.get("message") or error.get("code") or error)
                )
        return " | ".join(p for p in parts if p)[:400] or str(exc.msg or "")
