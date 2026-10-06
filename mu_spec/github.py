"""The GitHub REST client: create, edit and close an issue; declare and
undeclare what blocks it; read an issue's state before touching it.

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
import urllib.parse
import urllib.request
from typing import Callable

API_BASE = "https://api.github.com"

# Pinned rather than defaulted. Without this header GitHub is free to move the
# default forward and change a field under a client that never asked it to.
API_VERSION = "2026-03-10"

# GitHub rejects requests without one.
USER_AGENT = "mu-spec"

# Seconds to wait before every write, by default.
#
# GitHub rate-limits content creation *secondarily* -- separately from the
# 5000/hour budget, and specifically against bursts. An emission of `dark` is
# 358 writes (144 issues, 214 dependencies), which is exactly the shape that
# limit exists to stop, and the backoff below only helps once it is already too
# late. So the client paces itself rather than leaving each caller to remember.
#
# The rate-limit policy lives here in full: the pace, the backoff, obeying
# `Retry-After`, and telling a secondary limit apart from a permission error.
# A caller that had to know any of that would be a caller that gets it wrong.
#
# Pass `pace=0` to disable, which is for tests and for a single write -- not for
# a batch.
DEFAULT_PACE = 1.0

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
        pace: float = DEFAULT_PACE,
        timeout: float = 30.0,
    ) -> None:
        """`pace` is a wait before every write, and it is on by default.

        The cheap half of not tripping a secondary rate limit: `dark` needs 358
        writes, and sending them as fast as the socket allows is exactly what
        that limit exists to stop. The expensive half is the backoff in `_send`,
        which only runs once it is already too late. Both halves live in this
        module, because a caller that has to remember to pace itself is a
        caller that will not.
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
        self._identity: dict | None = None
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

    def whoami(self) -> dict:
        """Who the token authenticates as. `{"login", "type"}`.

        Every issue this unit creates is authored by this account, and a
        consumer deciding whether to hand an issue to an agent that writes
        code may reasonably authorize on the author -- a label can be applied
        by anyone with triage rights, an author cannot be forged. So the
        identity is a fact somebody has to be able to CHECK, not assume, and
        a token quietly rotated to a different account must be visible rather
        than silently breaking an allowlist.

        The token itself never leaves this module. `login` is a public name
        that appears on every issue the unit has ever created.
        """
        if self._identity is None:
            # Remembered on the INSTANCE. An account does not change under a
            # token, and the Ship panel polls every 900ms while a run is in
            # flight -- asking GitHub each time would spend rate limit on a
            # constant. A module-level cache would do the same job and leak
            # between tests, which is how the first version of this failed.
            raw = self._get("/user")
            self._identity = {
                "login": str(raw.get("login") or ""),
                "type": str(raw.get("type") or ""),
            }
        return self._identity

    def issue_state(self, repo: str, number: int) -> dict:
        """`{"state", "state_reason"}` for one issue.

        The only read this module performs, and it exists for one reason: a
        withdrawal must not overwrite somebody else's close. `not_planned` is
        this unit's marker and means withdrawn; `completed` belongs to whoever
        finished the work. A blind PATCH replaces the second with the first,
        which destroys the one signal the split exists to carry.

        This is not reacting to an issue. Nothing here decides anything from
        what comes back except whether the write it was already asked to make
        would damage something -- which is part of performing the withdrawal
        correctly rather than a second opinion about it.
        """
        raw = self._get(f"/repos/{repo}/issues/{int(number)}")
        return {
            "state": str(raw.get("state") or ""),
            "state_reason": str(raw.get("state_reason") or ""),
            # In the same read: a pickup label is the consumer saying "mine
            # now", and a second call per issue to learn it would double the
            # cost of every sync.
            "labels": [
                str(label.get("name") if isinstance(label, dict) else label)
                for label in raw.get("labels") or ()
            ],
        }

    def update_issue(
        self, repo: str, number: int, title: str, body: str,
        labels: "list[str] | tuple[str, ...] | None" = None,
    ) -> None:
        """Rewrite an issue nobody has picked up: title and body.

        `labels` REPLACES the whole set, and a sync must never send it: a
        consumer labelling the issue between our read and this write would
        have its pickup label erased, and the issue would look untouched to
        every Ship after. Use `add_labels` / `remove_label`, which touch only
        the names given."""
        payload: dict = {"title": title, "body": body}
        if labels is not None:
            payload["labels"] = list(labels)
        self._patch(f"/repos/{repo}/issues/{int(number)}", payload)

    def add_labels(self, repo: str, number: int, labels) -> None:
        """Add labels, leaving every other label exactly as it is."""
        self._post(f"/repos/{repo}/issues/{int(number)}/labels",
                   {"labels": list(labels)})

    def remove_label(self, repo: str, number: int, label: str) -> None:
        """Remove one label by name, leaving the rest."""
        self._request(
            "DELETE",
            f"/repos/{repo}/issues/{int(number)}/labels/"
            f"{urllib.parse.quote(label, safe='')}",
            None,
        )

    def remove_blocked_by(self, repo: str, blocked_number: int, blocker_id: int) -> None:
        """Undeclare an order the cut no longer has. Number in the path, the
        blocker's id at the end of it -- the same asymmetry as adding one.

        Not optional tidiness: a consumer treats a blocker closed `not_planned`
        as never satisfied, so a stale edge to a withdrawn unit blocks its
        dependent forever."""
        self._request(
            "DELETE",
            f"/repos/{repo}/issues/{int(blocked_number)}/dependencies/blocked_by/"
            f"{int(blocker_id)}",
            None,
        )

    def close_issue(self, repo: str, number: int, reason: str = "not_planned") -> None:
        """Close one issue, as `not_planned` rather than `completed`.

        The distinction is the whole point of carrying a reason: an issue
        withdrawn because the specification moved was never done, and marking
        it `completed` would put it in the same bucket as work that shipped.
        GitHub renders the two differently, and anything counting delivered
        work reads that field.

        Closing, not deleting. REST cannot delete an issue at all -- that is
        GraphQL and an admin-only mutation -- and closing is the reversible
        one, which is the right default for an operation whose whole purpose
        is undoing something. A closed issue also leaves the default `is:open`
        list, which is the visible outcome somebody asking for a rollback
        actually wants.
        """
        self._patch(f"/repos/{repo}/issues/{int(number)}",
                    {"state": "closed", "state_reason": reason})

    # -- the transport ------------------------------------------------------

    def _get(self, path: str) -> dict:
        return self._request("GET", path, None)

    def _patch(self, path: str, payload: dict) -> dict:
        return self._request("PATCH", path, payload)

    def _post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: dict | None) -> dict:
        request = urllib.request.Request(
            self._base + path,
            # A read carries no body. urllib decides GET vs POST from `data`
            # in some paths, so passing an empty object would send a GET with
            # a body, which GitHub answers inconsistently.
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method=method,
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
