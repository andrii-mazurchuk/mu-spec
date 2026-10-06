"""The GitHub client: two writes, and everything that can go wrong with them.

Stdlib only, and the opener, the clock and the sleep are all injected -- so the
whole of this file runs without a socket, and nothing here waits.

The two writes are asymmetric in a way worth stating once, because getting it
wrong fails several hundred issues into an emission:

- creating an issue returns BOTH an `id` (int64 database id) and a `number`
  (the `#123` a person sees), and they are different values;
- the dependency path takes the **number**, and the dependency body takes the
  **id** -- of the blocker.

So an emitter must keep the `id` of everything it creates, not just the number.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from mu_spec.github import (
    API_VERSION,
    GitHub,
    GitHubError,
    Issue,
)

REPO = "andrii-mazurchuk/dark"


class FakeResponse:
    def __init__(self, payload: dict, status: int = 201) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        return None


def http_error(code: int, message: str = "", headers: dict | None = None):
    return urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=code,
        msg=message or "error",
        hdrs=headers or {},
        fp=None,
    )


class Recorder:
    """An opener that records what it was asked and replays a script."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        result = self.responses.pop(0) if self.responses else FakeResponse({})
        if isinstance(result, BaseException):
            raise result
        return result

    @property
    def bodies(self):
        return [json.loads(r.data.decode("utf-8")) for r in self.requests if r.data]


def client(*responses, **kw):
    """Pacing off unless a test asks for it.

    The client paces itself by default -- that is its job -- but a test about
    backoff wants to see only the backoff, and one about a 403 wants `slept`
    empty to mean "did not retry" rather than "paced once".
    """
    recorder = Recorder(*responses)
    slept: list[float] = []
    kw.setdefault("pace", 0)
    gh = GitHub(
        "tok",
        opener=recorder,
        sleep=slept.append,
        **kw,
    )
    return gh, recorder, slept


# -- creating an issue -------------------------------------------------------


def test_creating_an_issue_returns_both_the_id_and_the_number():
    """The whole reason this returns an object rather than a number."""
    gh, rec, _ = client(FakeResponse(
        {"id": 3141592653, "number": 42, "html_url": "https://github.com/x/y/issues/42"}
    ))
    issue = gh.create_issue(REPO, "S-01 — declare.py", "the body", ["kind:test"])
    assert issue == Issue(id=3141592653, number=42,
                          url="https://github.com/x/y/issues/42")
    assert rec.requests[0].full_url == (
        f"https://api.github.com/repos/{REPO}/issues"
    )
    assert rec.bodies[0] == {
        "title": "S-01 — declare.py", "body": "the body", "labels": ["kind:test"],
    }


def test_every_request_carries_the_headers_github_requires():
    """`X-GitHub-Api-Version` pins the shape this client was written against --
    without it a future default could change a field under us. A `User-Agent`
    is required by GitHub and requests without one are rejected."""
    gh, rec, _ = client(FakeResponse({"id": 1, "number": 1}))
    gh.create_issue(REPO, "t", "b")
    headers = {k.lower(): v for k, v in rec.requests[0].header_items()}
    assert headers["authorization"] == "Bearer tok"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == API_VERSION
    assert "user-agent" in headers


def test_labels_are_omitted_entirely_when_there_are_none():
    """Not sent as `[]`. An empty array is a statement about labels, and this
    client should not make one it was not asked to."""
    gh, rec, _ = client(FakeResponse({"id": 1, "number": 1}))
    gh.create_issue(REPO, "t", "b")
    assert "labels" not in rec.bodies[0]


def test_a_token_is_required():
    """Issues can be READ anonymously, so an absent token fails at the first
    write rather than at construction unless we check. It is checked: the
    failure belongs where the cause is."""
    with pytest.raises(ValueError, match="token"):
        GitHub("")


# -- wiring a dependency -----------------------------------------------------


def test_the_dependency_path_takes_the_number_and_the_body_takes_the_id():
    """The trap. Passing the number in the body wires the wrong issue, or none,
    and says nothing about it."""
    gh, rec, _ = client(FakeResponse({}, status=201))
    gh.add_blocked_by(REPO, blocked_number=42, blocker_id=3141592653)
    assert rec.requests[0].full_url == (
        f"https://api.github.com/repos/{REPO}/issues/42/dependencies/blocked_by"
    )
    assert rec.bodies[0] == {"issue_id": 3141592653}


def test_there_is_only_one_direction_to_express_a_dependency():
    """GitHub offers no POST to `blocking`. "A blocks B" is expressed by
    posting A's id to B's `blocked_by`, which is exactly what `follows` means,
    so no inversion is needed anywhere."""
    assert not hasattr(GitHub, "add_blocking")


# -- rate limits, which are not hypothetical ---------------------------------


def test_a_429_is_retried_after_the_delay_the_response_names():
    """`dark` needs 358 writes. Secondary rate limiting is what a tight loop
    of those meets, and `Retry-After` is GitHub telling us exactly how long to
    wait -- guessing instead is how a client gets itself blocked."""
    gh, rec, slept = client(
        http_error(429, "slow down", {"Retry-After": "7"}),
        FakeResponse({"id": 1, "number": 1}),
    )
    issue = gh.create_issue(REPO, "t", "b")
    assert issue.number == 1
    assert slept == [7.0]
    assert len(rec.requests) == 2


def test_a_secondary_rate_limit_arrives_as_403_and_is_retried():
    """GitHub uses 403 for secondary rate limiting as well as for permission
    denied, so the body is what distinguishes them."""
    gh, rec, slept = client(
        http_error(403, "You have exceeded a secondary rate limit"),
        FakeResponse({"id": 1, "number": 1}),
    )
    assert gh.create_issue(REPO, "t", "b").number == 1
    assert len(rec.requests) == 2
    assert slept, "a retry with no wait is what caused the rate limit"


def test_a_permission_denied_403_fails_immediately():
    """The other 403. Retrying a token that lacks `Issues: write` four times
    changes nothing, takes four times as long, and buries the real cause."""
    gh, rec, slept = client(http_error(403, "Resource not accessible by personal access token"))
    with pytest.raises(GitHubError) as exc:
        gh.create_issue(REPO, "t", "b")
    assert exc.value.status == 403
    assert len(rec.requests) == 1, "no retry"
    assert slept == []


def test_a_server_error_is_retried_with_growing_backoff():
    gh, rec, slept = client(
        http_error(502), http_error(502), FakeResponse({"id": 1, "number": 1})
    )
    assert gh.create_issue(REPO, "t", "b").number == 1
    assert len(rec.requests) == 3
    assert slept == sorted(slept) and len(slept) == 2, "backoff grows"


def test_retries_are_finite_and_the_last_failure_is_the_one_reported():
    gh, rec, _ = client(*[http_error(502, "gateway")] * 9, attempts=3)
    with pytest.raises(GitHubError) as exc:
        gh.create_issue(REPO, "t", "b")
    assert len(rec.requests) == 3
    assert exc.value.status == 502


def test_a_404_is_not_retried_because_it_will_not_become_a_200():
    """The likeliest real cause is a repo that does not exist or a token that
    cannot see it, and both of those are configuration."""
    gh, rec, _ = client(http_error(404, "Not Found"))
    with pytest.raises(GitHubError):
        gh.create_issue(REPO, "t", "b")
    assert len(rec.requests) == 1


def test_a_422_is_not_retried_and_carries_what_github_objected_to():
    """Validation. An oversized body or a malformed field arrives here, and the
    message is the only thing that says which."""
    gh, _, _ = client(http_error(422, "Validation Failed"))
    with pytest.raises(GitHubError, match="422"):
        gh.create_issue(REPO, "t", "b")


def test_the_pace_between_writes_is_settable_and_waits_before_each():
    """The cheap half of not tripping a secondary limit: do not send 358 writes
    as fast as the socket allows."""
    gh, rec, slept = client(
        FakeResponse({"id": 1, "number": 1}),
        FakeResponse({"id": 2, "number": 2}),
        pace=0.25,
    )
    gh.create_issue(REPO, "a", "b")
    gh.create_issue(REPO, "c", "d")
    assert slept == [0.25, 0.25]


def test_pacing_is_on_by_default():
    """The whole point of it living here. An emission is 358 writes and the
    caller should not have to remember -- a client that only paces when asked
    is a client that bursts the first time somebody forgets."""
    from mu_spec.github import DEFAULT_PACE

    slept: list[float] = []
    gh = GitHub("tok", opener=Recorder(FakeResponse({"id": 1, "number": 1})),
                sleep=slept.append)
    gh.create_issue(REPO, "t", "b")
    assert slept == [DEFAULT_PACE]


def test_pacing_can_be_turned_off_for_a_single_write():
    gh, _, slept = client(FakeResponse({"id": 1, "number": 1}), pace=0)
    gh.create_issue(REPO, "t", "b")
    assert slept == []


# -- the network itself ------------------------------------------------------


def test_an_unreachable_host_is_a_GitHubError_not_a_urllib_error():
    """Everything above this client should see one exception type. A caller
    catching `URLError` as well is a caller that will miss the next thing
    urllib decides to raise."""
    gh, _, _ = client(urllib.error.URLError("no route to host"), attempts=1)
    with pytest.raises(GitHubError, match="unreachable"):
        gh.create_issue(REPO, "t", "b")


def test_a_response_that_is_not_json_is_reported_as_such():
    class Garbage:
        status = 201

        def read(self):
            return b"<html>a proxy said no</html>"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    gh, _, _ = client(Garbage(), attempts=1)
    with pytest.raises(GitHubError, match="not JSON"):
        gh.create_issue(REPO, "t", "b")


def test_a_created_issue_missing_its_id_is_refused():
    """Rather than defaulting to 0 and wiring every dependency to nothing."""
    gh, _, _ = client(FakeResponse({"number": 42}))
    with pytest.raises(GitHubError, match="id"):
        gh.create_issue(REPO, "t", "b")


def test_close_issue_is_a_patch_marked_not_planned(_unused=None):
    """Withdrawn is not done. An issue closed because the specification moved
    was never completed, and `completed` would file it beside work that
    actually shipped -- which is what anything counting delivered work reads.
    """
    gh, recorder, _ = client(FakeResponse({}, status=200))
    gh.close_issue(REPO, 41)
    request = recorder.requests[0]
    assert request.get_method() == "PATCH"
    assert request.full_url.endswith(f"/repos/{REPO}/issues/41")
    assert recorder.bodies[0] == {"state": "closed", "state_reason": "not_planned"}


def test_close_issue_carries_the_same_headers_as_a_create():
    """One transport, so a PATCH cannot drift from a POST in auth or API
    version -- the failure that would produce is a 401 on rollback only."""
    gh, recorder, _ = client(FakeResponse({}, status=200))
    gh.close_issue(REPO, 7)
    headers = recorder.requests[0].headers
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-github-api-version"] == API_VERSION


def test_a_failed_close_raises_the_one_exception_type():
    """Same contract as the creates: a caller that also had to know about
    HTTPError would miss whatever urllib raises next."""
    gh, _recorder, _ = client(http_error(404, "Not Found"))
    with pytest.raises(GitHubError) as caught:
        gh.close_issue(REPO, 999)
    assert caught.value.status == 404


def test_whoami_reads_the_account_the_token_belongs_to():
    """Every issue is authored by this account, and a consumer may authorize
    on the author -- a label needs only triage rights, an author cannot be
    forged. So the identity has to be checkable rather than assumed."""
    gh, recorder, _ = client(FakeResponse({"login": "andrii-mazurchuk",
                                           "type": "User"}, status=200))
    who = gh.whoami()
    assert who == {"login": "andrii-mazurchuk", "type": "User"}
    request = recorder.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url.endswith("/user")


def test_a_read_sends_no_body():
    """urllib infers the method from `data` in some paths, so an empty object
    would send a GET carrying a body, which GitHub answers inconsistently."""
    gh, recorder, _ = client(FakeResponse({"login": "x", "type": "Bot"}, status=200))
    gh.whoami()
    assert recorder.requests[0].data is None


def test_a_bot_identity_comes_back_verbatim():
    """A GitHub App authors as `something[bot]` and the allowlist is a string
    equality check, so the login is never cleaned up or prettified."""
    gh, _r, _ = client(FakeResponse({"login": "mu-spec[bot]", "type": "Bot"},
                                    status=200))
    assert gh.whoami()["login"] == "mu-spec[bot]"


def test_an_unusable_identity_response_does_not_invent_a_login():
    gh, _r, _ = client(FakeResponse({}, status=200))
    assert gh.whoami() == {"login": "", "type": ""}


def test_the_identity_is_asked_for_once_per_client():
    """The Ship panel polls every 900ms while a run is in flight. Asking
    GitHub each time would spend rate limit on a constant that cannot change
    under a token."""
    gh, recorder, _ = client(FakeResponse({"login": "a", "type": "User"}, status=200))
    assert gh.whoami()["login"] == "a"
    assert gh.whoami()["login"] == "a"
    assert gh.whoami()["login"] == "a"
    assert len(recorder.requests) == 1


def test_a_second_client_asks_again():
    """Instance state, not module state. A module-level cache leaked between
    tests -- a failing lookup returned an earlier test's answer -- and would
    also survive a token being rotated to another account."""
    first, _r1, _ = client(FakeResponse({"login": "a", "type": "User"}, status=200))
    second, r2, _ = client(FakeResponse({"login": "b", "type": "User"}, status=200))
    assert first.whoami()["login"] == "a"
    assert second.whoami()["login"] == "b"
    assert len(r2.requests) == 1


# -- what a sync needs: an issue's labels, an edit, an edge removed ---------


def test_issue_state_carries_the_labels_too():
    """`touched` is read from state, reason and labels in ONE call. A pickup
    label is the consumer saying "mine now", and a second read per issue to
    learn it would double the cost of every sync."""
    gh, _rec, _ = client(FakeResponse({
        "state": "open", "state_reason": None,
        "labels": [{"name": "cto:admitted"}, {"name": "kind:test"}],
    }, status=200))
    assert gh.issue_state(REPO, 5) == {
        "state": "open", "state_reason": "",
        "labels": ["cto:admitted", "kind:test"],
    }


def test_update_issue_is_one_patch_of_title_body_and_labels():
    gh, rec, _ = client(FakeResponse({}, status=200))
    gh.update_issue(REPO, 12, "S-01 — a.py", "new body", ["kind:test"])
    request = rec.requests[0]
    assert request.get_method() == "PATCH"
    assert request.full_url.endswith(f"/repos/{REPO}/issues/12")
    assert rec.bodies[0] == {
        "title": "S-01 — a.py", "body": "new body", "labels": ["kind:test"],
    }


def test_removing_a_dependency_names_the_blocked_number_and_the_blocker_id():
    """The same asymmetry as adding one: number in the path, id of the blocker
    at the end of it."""
    gh, rec, _ = client(FakeResponse({}, status=200))
    gh.remove_blocked_by(REPO, 12, 3141592653)
    request = rec.requests[0]
    assert request.get_method() == "DELETE"
    assert request.full_url.endswith(
        f"/repos/{REPO}/issues/12/dependencies/blocked_by/3141592653"
    )
    assert request.data is None
