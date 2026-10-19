"""Ship as a sync: one live issue per (repo, unit key), across every cut.

docs/TICKETS.md is the protocol these pin. The fake below holds real issue
state -- open or closed, why, which labels, which dependencies -- because every
rule here is about what is already on the tracker, and a recorder that only
lists calls cannot say whether an issue somebody picked up was rewritten.
"""

from __future__ import annotations

from mu_spec.emit import emit, read_log, rollback, standing
from mu_spec.github import GitHubError, Issue

REPO = "o/dark"
PICKUP = ("cto:admitted",)

# S-01-T, then S-01 (follows its test unit), then S-02 (follows S-01). Three
# waves, one unit each.
ORDER = ["S-01-T", "S-01", "S-02"]
EDGES = {"S-01": ("S-01-T",), "S-02": ("S-01",)}
WAVES = [["S-01-T"], ["S-01"], ["S-02"]]


def payload(key, follows=(), body="the contract"):
    anchor = key.removesuffix("-T")
    return {
        "project": "dark",
        "issued": True,
        "unit": {"key": key, "anchor": anchor, "entries": [anchor],
                 "modules": [f"{key}.py"], "slices": ["core"], "size": {}},
        "write_set": [f"{key}.py"],
        "entries": [{"id": anchor, "title": f"{key} title", "body": body}],
        "justification": [], "read_set": [], "cross_cutting": [],
        "file_scope": {}, "follows": list(follows), "followed_by": [],
        "overlap": [], "tests": [], "tests_pending": [],
        "audit": {"editable_paths": [f"{key}.py"], "rule": "stay inside"},
    }


class Tracker:
    """A repository's issues, as GitHub would hold them."""

    def __init__(self, start=0):
        self.n = start
        self.issues: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.fail_reads: set[int] = set()
        # Labels to add to an issue the moment after it is read -- the
        # consumer admitting it inside the gap between read and write.
        self.admit_after_read: set[int] = set()

    # -- the client surface --
    def create_issue(self, repo, title, body, labels=()):
        self.n += 1
        self.issues[self.n] = {"title": title, "body": body,
                               "labels": list(labels), "state": "open",
                               "state_reason": "", "blocked_by": set()}
        self.calls.append(("create", self.n))
        return Issue(id=1000 + self.n, number=self.n, url=f"u/{self.n}")

    def add_blocked_by(self, repo, number, blocker_id):
        self.issues[number]["blocked_by"].add(blocker_id - 1000)
        self.calls.append(("wire", number, blocker_id - 1000))

    def remove_blocked_by(self, repo, number, blocker_id):
        self.issues[number]["blocked_by"].discard(blocker_id - 1000)
        self.calls.append(("unwire", number, blocker_id - 1000))

    def blocked_by(self, repo, number):
        self.calls.append(("read", number))
        return [{"id": 1000 + n, "number": n, "state": self.issues[n]["state"],
                 "labels": list(self.issues[n]["labels"])}
                for n in sorted(self.issues[number]["blocked_by"])]

    def issue_state(self, repo, number):
        if number in self.fail_reads:
            raise GitHubError("GitHub answered 502", status=502)
        issue = self.issues[number]
        state = {"state": issue["state"], "state_reason": issue["state_reason"],
                 "labels": list(issue["labels"]), "body": issue["body"]}
        if number in self.admit_after_read:
            self.admit_after_read.discard(number)
            issue["labels"].append("cto:admitted")
        return state

    def open_issues(self, repo):
        self.calls.append(("list", 0))
        return [{"id": 1000 + n, "number": n, "url": f"u/{n}", "title": i["title"],
                 "labels": list(i["labels"]), "body": i["body"]}
                for n, i in sorted(self.issues.items()) if i["state"] == "open"]

    def update_issue(self, repo, number, title, body, labels=None):
        self.issues[number].update(title=title, body=body)
        if labels is not None:
            self.issues[number]["labels"] = list(labels)
        self.calls.append(("edit", number))

    def add_labels(self, repo, number, labels):
        for label in labels:
            if label not in self.issues[number]["labels"]:
                self.issues[number]["labels"].append(label)

    def remove_label(self, repo, number, label):
        self.issues[number]["labels"].remove(label)

    def close_issue(self, repo, number, reason="not_planned"):
        self.issues[number].update(state="closed", state_reason=reason)
        self.calls.append(("close", number))

    # -- what a test reads --
    def writes(self):
        return [c for c in self.calls if c[0] not in ("read", "list")]

    def open_numbers(self):
        return sorted(n for n, i in self.issues.items() if i["state"] == "open")


def ship(tmp_path, tracker, *, order=ORDER, edges=EDGES, waves=WAVES,
         horizon=None, bodies=None, pickup=PICKUP, labels=(), renamed=None):
    bodies = bodies or {}
    return emit(renamed=renamed,
        log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3,
        order=list(order), edges=edges, waves=waves, horizon=horizon,
        fetch=lambda k: payload(k, edges.get(k, ()), bodies.get(k, "the contract")),
        client=tracker, now_fn=lambda: 1.0, pickup=pickup, labels=labels, project="dark",
    )


# -- identity across cuts -----------------------------------------------------


def test_a_new_cut_that_says_the_same_thing_creates_nothing(tmp_path):
    """The 288-issue defect. A new cut used to look never-emitted."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.calls.clear()
    result = emit(
        log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=4,
        order=ORDER, edges=EDGES, waves=WAVES, fetch=lambda k: payload(k, EDGES.get(k, ())),
        client=gh, now_fn=lambda: 2.0, pickup=PICKUP,
    )
    assert gh.calls == [], "not even a read: unchanged costs nothing"
    assert result["counts"]["created"] == 0
    assert result["counts"]["edited"] == 0


def test_standing_is_per_repo_not_per_cut(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    live, wired = standing(tmp_path / "emissions.jsonl", REPO)
    assert {k: v["number"] for k, v in live.items()} == {"S-01-T": 1, "S-01": 2, "S-02": 3}
    assert live["S-01"]["fingerprint"]
    assert wired == {"S-01<-S-01-T", "S-02<-S-01"}


# -- edit in place ------------------------------------------------------------


def test_a_changed_untouched_issue_is_edited_in_place(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    result = ship(tmp_path, gh, bodies={"S-02": "a corrected contract"})
    assert [e["key"] for e in result["edited"]] == ["S-02"]
    assert "a corrected contract" in gh.issues[3]["body"]
    assert gh.n == 3, "same issue number, nothing created"


def test_an_edit_stamps_the_new_batch(tmp_path):
    import json
    gh = Tracker()
    first = ship(tmp_path, gh)
    second = ship(tmp_path, gh, bodies={"S-02": "changed"})
    block = gh.issues[3]["body"].split("```json")[-1].split("```")[0]
    assert json.loads(block)["batch"] == second["batch"] != first["batch"]
    assert json.loads(gh.issues[1]["body"].split("```json")[-1].split("```")[0])[
        "batch"] == first["batch"], "an unchanged issue keeps its batch"


def test_a_picked_up_issue_is_never_edited_only_reported(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3]["labels"].append("cto:admitted")
    result = ship(tmp_path, gh, bodies={"S-02": "changed"})
    assert ("edit", 3) not in gh.calls
    assert [t["key"] for t in result["touched"]] == ["S-02"]
    assert "cto:admitted" in result["touched"][0]["reason"]


def test_a_touched_issue_is_reported_again_until_released(tmp_path):
    """Its old fingerprint is kept, so the change stays visible. Released --
    the label removed -- the next Ship edits it."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3]["labels"].append("cto:admitted")
    ship(tmp_path, gh, bodies={"S-02": "changed"})
    again = ship(tmp_path, gh, bodies={"S-02": "changed"})
    assert [t["key"] for t in again["touched"]] == ["S-02"]
    gh.issues[3]["labels"].remove("cto:admitted")
    released = ship(tmp_path, gh, bodies={"S-02": "changed"})
    assert [e["key"] for e in released["edited"]] == ["S-02"]


def test_an_issue_somebody_closed_is_settled_not_reported(tmp_path):
    """A closed issue whose CONTEXT changed -- labels, blockers, renamed ids --
    is done with: never edited, and not a warning either. `touched` is for
    open work somebody holds, the one case where releasing it means anything."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, labels=("team:x",))
    assert ("edit", 3) not in gh.calls
    assert result["touched"] == [] and result["follow_ups"] == []
    assert ("S-02", 3) in [(t["key"], t["number"]) for t in result["settled"]]


def test_a_closed_issue_whose_contract_changed_gets_a_follow_up(tmp_path):
    """The work it asked for is merged, and the contract now asks for more.
    Silence would lose that work; a warning on a closed issue asks for an
    action nobody can take. A fresh open issue is the only shape that gets
    it built."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, bodies={"S-02": "the contract, and two new settings"})
    assert ("edit", 3) not in gh.calls, "the closed issue is left as it was"
    [follow] = result["follow_ups"]
    assert follow["key"] == "S-02" and follow["follow_up_of"] == 3 and follow["number"] == 4
    assert "follow-up to #3" in gh.issues[4]["title"]
    assert "#3" in gh.issues[4]["body"] and "two new settings" in gh.issues[4]["body"]
    assert gh.issues[4]["state"] == "open"
    assert result["settled"] == [] and result["touched"] == []
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert live["S-02"]["number"] == 4


def test_a_follow_up_is_opened_once_and_then_edited_in_place(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    ship(tmp_path, gh, bodies={"S-02": "more"})
    again = ship(tmp_path, gh, bodies={"S-02": "more"})
    assert again["created"] == [] and again["follow_ups"] == [] and gh.n == 4
    edited = ship(tmp_path, gh, bodies={"S-02": "more still"})
    assert [e["number"] for e in edited["edited"]] == [4]


def test_a_dependent_waits_on_the_follow_up_not_the_merged_issue(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[2].update(state="closed", state_reason="completed")       # S-01
    result = ship(tmp_path, gh, bodies={"S-01": "a changed contract"})
    [follow] = result["follow_ups"]
    assert ("wire", 3, follow["number"]) in gh.calls, "S-02 now blocked by the follow-up"


def test_a_dependent_stops_naming_the_merged_key_once_its_blocker_has_a_follow_up(tmp_path):
    """`blocked_by_was` says a blocker closed `completed` under an old key
    counts as done. For a blocker with a follow-up that is exactly wrong: the
    old key's work is merged and the follow-up's is not."""
    import json
    gh = Tracker()

    def fetch(key, bodies):
        unit = payload(key, EDGES.get(key, ()), bodies.get(key, "the contract"))
        if key == "S-02":
            unit["follows_was"] = {"S-01": ["S-00"]}
        return unit

    def press(bodies):
        return emit(log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3,
                    order=list(ORDER), edges=EDGES, waves=WAVES, horizon=None,
                    fetch=lambda k: fetch(k, bodies), client=gh, now_fn=lambda: 1.0,
                    pickup=PICKUP)

    press({})
    block = lambda n: json.loads(gh.issues[n]["body"].split("```json")[-1].split("```")[0])
    assert block(3)["blocked_by_was"] == {"S-01": ["S-00"]}
    gh.issues[2].update(state="closed", state_reason="completed")
    press({"S-01": "a changed contract"})
    assert block(3)["blocked_by_was"] == {}


def test_a_blocker_whose_follow_up_predates_its_rename_keeps_its_old_key(tmp_path):
    """The blocker's issue is an old follow-up opened under the key it was
    renamed from, and nothing new was opened. That old key is the only name
    the issue answers to, so cutting it leaves the dependent waiting forever."""
    import json
    gh = Tracker()

    def press():
        return emit(log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3,
                    order=list(ORDER), edges=EDGES, waves=WAVES, horizon=None,
                    fetch=lambda k: {**payload(k, EDGES.get(k, ())),
                                     **({"follows_was": {"S-01": ["S-00"]}} if k == "S-02" else {})},
                    client=gh, now_fn=lambda: 1.0, pickup=PICKUP)

    press()
    log = tmp_path / "emissions.jsonl"
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    lines[-1]["issues"]["S-01"].update(follow_up_of=1, labels=["mu-spec:S-00", "kind:implementation"])
    log.write_text("".join(json.dumps(l) + chr(10) for l in lines))
    press()
    block = json.loads(gh.issues[3]["body"].split("```json")[-1].split("```")[0])
    assert block["blocked_by_was"] == {"S-01": ["S-00"]}


def test_a_legacy_record_compares_against_the_closed_issues_own_text(tmp_path):
    """Records written before contracts were fingerprinted carry none, so the
    closed issue's Contract section is read in the same call as its state."""
    import json
    gh = Tracker()
    ship(tmp_path, gh)
    log = tmp_path / "emissions.jsonl"
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    for line in lines:
        for issue in (line.get("issues") or {}).values():
            issue.pop("contract", None)
    log.write_text("".join(json.dumps(l) + chr(10) for l in lines))
    gh.issues[3].update(state="closed", state_reason="completed")
    same = ship(tmp_path, gh, labels=("team:x",))
    assert same["follow_ups"] == [] and [t["key"] for t in same["settled"]] == ["S-02"]
    changed = ship(tmp_path, gh, bodies={"S-02": "grown"})
    assert [f["key"] for f in changed["follow_ups"]] == ["S-02"]


def test_a_change_below_a_heading_inside_the_spec_body_is_a_contract_change(tmp_path):
    """DARK S-299's body has `## ` headings of its own. The contract ended at the
    first one, so everything below it -- most of the entry -- was context, and a
    rewritten schema settled as 'contract did not change'."""
    gh = Tracker()
    ship(tmp_path, gh, bodies={"S-02": "intro\n\n## Section\n\nthe old rule"})
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, bodies={"S-02": "intro\n\n## Section\n\nthe new rule"})
    assert [f["key"] for f in result["follow_ups"]] == ["S-02"]


def test_a_record_fingerprinted_the_old_way_compares_against_the_issues_own_text(tmp_path):
    """A record written before the contract ran to `## Files you may write`
    holds a hash of the first chunk only. Comparing that with the full section
    would open a follow-up for every closed issue whose body has a heading."""
    import json
    from mu_spec.emit import _contract_v1
    gh = Tracker()
    body = "intro\n\n## Section\n\nthe rule"
    ship(tmp_path, gh, bodies={"S-02": body})
    log = tmp_path / "emissions.jsonl"
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    for line in lines:
        for issue in (line.get("issues") or {}).values():
            if issue.get("contract"):
                issue["contract"] = _contract_v1(gh.issues[issue["number"]]["body"])
    log.write_text("".join(json.dumps(l) + chr(10) for l in lines))
    gh.issues[3].update(state="closed", state_reason="completed")
    same = ship(tmp_path, gh, bodies={"S-02": body}, labels=("team:x",))
    assert same["follow_ups"] == [] and [t["key"] for t in same["settled"]] == ["S-02"]
    changed = ship(tmp_path, gh, bodies={"S-02": "intro\n\n## Section\n\nthe rule, grown"})
    assert [f["key"] for f in changed["follow_ups"]] == ["S-02"]


def test_a_closed_issue_whose_unit_left_the_cut_is_settled_not_reported(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, order=["S-01-T", "S-01"], edges={"S-01": ("S-01-T",)},
                  waves=[["S-01-T"], ["S-01"]])
    assert ("close", 3) not in [c[:2] for c in gh.calls]
    assert result["touched"] == [] and result["withdrawn"] == []
    assert [t["key"] for t in result["settled"]] == ["S-02"]


def test_without_a_pickup_label_nothing_is_edited(tmp_path):
    """Without a pickup signal, untouched cannot be told from admitted."""
    gh = Tracker()
    ship(tmp_path, gh, pickup=())
    result = ship(tmp_path, gh, bodies={"S-02": "changed"}, pickup=())
    assert ("edit", 3) not in gh.calls
    assert "pickup" in result["touched"][0]["reason"]


def test_an_admission_inside_the_gap_is_reported_never_silent(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.admit_after_read.add(3)
    result = ship(tmp_path, gh, bodies={"S-02": "changed"})
    assert [r["key"] for r in result["raced"]] == ["S-02"]
    assert "cto:admitted" in gh.issues[3]["labels"], "the edit never erases it"


def test_an_unreadable_issue_is_left_alone(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.fail_reads.add(3)
    result = ship(tmp_path, gh, bodies={"S-02": "changed"})
    assert ("edit", 3) not in gh.calls
    assert "could not read" in result["touched"][0]["reason"]


def test_an_edit_keeps_labels_that_are_not_ours(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh, labels=("team:cto",))
    gh.issues[3]["labels"].append("priority:high")
    ship(tmp_path, gh, bodies={"S-02": "changed"}, labels=("team:cto",))
    labels = gh.issues[3]["labels"]
    assert "priority:high" in labels and "team:cto" in labels
    assert "mu-spec:S-02" in labels


# -- units that leave the cut -------------------------------------------------


def test_a_unit_gone_from_the_cut_is_withdrawn(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    result = ship(tmp_path, gh, order=["S-01-T", "S-01"], edges={"S-01": ("S-01-T",)},
                  waves=[["S-01-T"], ["S-01"]])
    assert [w["key"] for w in result["withdrawn"]] == ["S-02"]
    assert gh.issues[3]["state_reason"] == "not_planned"
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert "S-02" not in live


def test_a_picked_up_unit_gone_from_the_cut_is_reported_not_closed(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3]["labels"].append("cto:admitted")
    result = ship(tmp_path, gh, order=["S-01-T", "S-01"], edges={"S-01": ("S-01-T",)},
                  waves=[["S-01-T"], ["S-01"]])
    assert gh.issues[3]["state"] == "open"
    assert [t["key"] for t in result["touched"]] == ["S-02"]


# -- dependencies follow the cut ---------------------------------------------


def test_a_dependency_the_cut_dropped_is_removed(tmp_path):
    """Not tidiness: a blocker closed not_planned is never satisfied, so a
    stale edge to a withdrawn unit blocks its dependent forever."""
    gh = Tracker()
    ship(tmp_path, gh)
    result = ship(tmp_path, gh, edges={"S-01": ("S-01-T",), "S-02": ()},
                  waves=[["S-01-T", "S-02"], ["S-01"]])
    assert ("unwire", 3, 2) in gh.calls
    assert gh.issues[3]["blocked_by"] == set()
    _live, wired = standing(tmp_path / "emissions.jsonl", REPO)
    assert wired == {"S-01<-S-01-T"}


def test_withdrawing_a_blocker_unwires_its_live_dependent(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    ship(tmp_path, gh, order=["S-01-T", "S-02"], edges={}, waves=[["S-01-T", "S-02"]])
    assert gh.issues[2]["state"] == "closed"
    assert gh.issues[3]["blocked_by"] == set(), "S-02 no longer waits on a withdrawn S-01"
    # The consumer reads blocked_by from the body's block, never from GitHub's
    # native dependency -- so the edit must rewrite it there too.
    import json
    block = gh.issues[3]["body"].split("```json")[-1].split("```")[0]
    assert json.loads(block)["blocked_by"] == []


# -- a press that lost its record --------------------------------------------


def _lose_records_after(tmp_path, keep):
    log = tmp_path / "emissions.jsonl"
    log.write_text("".join(l + chr(10) for l in log.read_text().splitlines()[:keep]))


class Dies(Tracker):
    """GitHub stops answering while the order is being declared."""

    def add_blocked_by(self, repo, number, blocker_id):
        raise TimeoutError("The read operation timed out")


def test_a_press_that_dies_midway_still_records_what_it_created(tmp_path):
    """DARK, cut 8: a read timeout escaped during wiring, the record was never
    written, and the retry created the same thirty issues again."""
    import pytest
    gh = Dies()
    with pytest.raises(TimeoutError):
        ship(tmp_path, gh)
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert sorted(live) == sorted(ORDER), "every issue that landed is on record"
    gh.__class__ = Tracker
    again = ship(tmp_path, gh)
    assert again["created"] == [] and gh.n == 3


def test_an_open_issue_for_a_key_is_adopted_not_created_twice(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    _lose_records_after(tmp_path, 0)
    result = ship(tmp_path, gh)
    assert result["created"] == [] and gh.n == 3
    assert sorted(a["number"] for a in result["adopted"]) == [1, 2, 3]
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert {k: v["number"] for k, v in live.items()} == {"S-01-T": 1, "S-01": 2, "S-02": 3}


def test_an_orphan_duplicate_is_swept_and_the_first_one_kept(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    _lose_records_after(tmp_path, 0)
    gh.n = 4
    gh.issues[4] = {**gh.issues[2], "labels": list(gh.issues[2]["labels"]), "blocked_by": set()}
    result = ship(tmp_path, gh)
    assert [a["number"] for a in result["adopted"] if a["key"] == "S-01"] == [2]
    assert [w["number"] for w in result["swept"]] == [4]
    assert gh.issues[4]["state"] == "closed" and gh.issues[4]["state_reason"] == "not_planned"
    assert gh.issues[2]["state"] == "open"


def test_a_picked_up_orphan_is_left_alone_and_reported(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.n = 4
    gh.issues[4] = {**gh.issues[2], "labels": list(gh.issues[2]["labels"]) + ["cto:admitted"],
                    "blocked_by": set()}
    result = ship(tmp_path, gh)
    assert result["swept"] == [] and gh.issues[4]["state"] == "open"
    assert [t["number"] for t in result["touched"]] == [4]


def test_an_issue_of_another_project_is_never_swept(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.n = 4
    gh.issues[4] = {**gh.issues[2], "body": gh.issues[2]["body"].replace('"project": "dark"', '"project": "other"'),
                    "labels": list(gh.issues[2]["labels"]), "blocked_by": set()}
    assert '"project": "other"' in gh.issues[4]["body"]
    result = ship(tmp_path, gh)
    assert result["swept"] == [] and gh.issues[4]["state"] == "open"


def test_an_open_follow_up_whose_record_was_lost_is_adopted(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    ship(tmp_path, gh, bodies={"S-02": "grown"})                 # follow-up #4
    _lose_records_after(tmp_path, 1)
    result = ship(tmp_path, gh, bodies={"S-02": "grown"})
    assert result["follow_ups"] == [] and gh.n == 4
    assert [(a["key"], a["number"]) for a in result["adopted"]] == [("S-02", 4)]
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert live["S-02"]["number"] == 4 and live["S-02"]["follow_up_of"] == 3


class AlreadyLinked(Tracker):
    """GitHub refuses a blocked-by link that already exists -- what a press
    whose record was lost leaves behind."""

    def add_blocked_by(self, repo, number, blocker_id):
        if blocker_id - 1000 in self.issues[number]["blocked_by"]:
            raise GitHubError("GitHub answered 422: An error occurred while adding the blocking "
                              "issue to the issue. Validation failed: Target issue has already "
                              "been taken", status=422)
        super().add_blocked_by(repo, number, blocker_id)


def test_an_order_github_already_holds_counts_as_wired(tmp_path):
    """DARK's repair press reported 144 orders 'not declared' that were all in
    place. Unrecorded, every later press would have tried them again."""
    gh = AlreadyLinked()
    ship(tmp_path, gh)
    _lose_records_after(tmp_path, 0)
    mark = len(gh.calls)
    result = ship(tmp_path, gh)
    assert result["unwired"] == [] and not [c for c in gh.calls[mark:] if c[0] == "wire"]
    assert len(standing(tmp_path / "emissions.jsonl", REPO)[1]) == 2
    again = ship(tmp_path, gh)
    assert again["wired"] == [] and again["unwired"] == []


class AlreadyLinkedUnread(AlreadyLinked):
    """The native read that would have shown the links fails, so the press
    asks for them again and GitHub answers 422."""

    def blocked_by(self, repo, number):
        raise GitHubError("GitHub answered 502", status=502)


def test_a_422_already_taken_is_recorded_as_wired_not_unwired(tmp_path):
    """796feb9: since the native read, only a failed read reaches the 422 --
    and there it must still count as the order in place."""
    gh = AlreadyLinkedUnread()
    ship(tmp_path, gh)
    _lose_records_after(tmp_path, 0)
    result = ship(tmp_path, gh)
    assert result["unwired"] == []
    assert len(standing(tmp_path / "emissions.jsonl", REPO)[1]) == 2


def test_progress_reaches_the_total_when_a_follow_up_is_adopted(tmp_path):
    """DARK showed 'processed' stopping short of the unit count: a follow-up
    adopted rather than created was processed but never counted."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    ship(tmp_path, gh, bodies={"S-02": "grown"})
    _lose_records_after(tmp_path, 1)
    seen = []
    emit(log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3, order=list(ORDER),
         edges=EDGES, waves=WAVES, fetch=lambda k: payload(k, EDGES.get(k, ()), "grown" if k == "S-02" else "the contract"),
         client=gh, now_fn=lambda: 1.0, pickup=PICKUP, project="dark", on_progress=seen.append)
    last = [s for s in seen if s["step"] != "wiring"][-1]
    assert last["processed"] == last["total"] == 3


def test_a_follow_up_the_contract_no_longer_calls_for_is_swept(tmp_path):
    """DARK: 46 follow-ups opened against an empty comparison. With the
    contract unchanged the key is settled, and its open follow-up is an
    orphan."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    gh.n = 4
    gh.issues[4] = {**gh.issues[3], "state": "open", "state_reason": "",
                    "title": gh.issues[3]["title"] + " (follow-up to #3)",
                    "labels": list(gh.issues[3]["labels"]), "blocked_by": set()}
    result = ship(tmp_path, gh, labels=("team:x",))
    assert result["follow_ups"] == [] and [w["number"] for w in result["swept"]] == [4]


# -- the horizon --------------------------------------------------------------


def test_the_horizon_creates_only_the_next_n_waves(tmp_path):
    gh = Tracker()
    first = ship(tmp_path, gh, horizon=1)
    assert [c["key"] for c in first["created"]] == ["S-01-T"]
    second = ship(tmp_path, gh, horizon=1)
    assert [c["key"] for c in second["created"]] == ["S-01"]
    assert ("wire", 2, 1) in gh.calls, "wired to the issue the earlier press made"
    third = ship(tmp_path, gh, horizon=5)
    assert [c["key"] for c in third["created"]] == ["S-02"]


def test_the_horizon_never_holds_back_an_edit(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh, horizon=2)
    result = ship(tmp_path, gh, horizon=1, bodies={"S-01-T": "changed"})
    assert [e["key"] for e in result["edited"]] == ["S-01-T"]
    assert [c["key"] for c in result["created"]] == ["S-02"]


ORDER4 = ["S-01-T", "S-01", "S-02", "S-03"]
EDGES4 = {"S-01": ("S-01-T",), "S-02": ("S-01",), "S-03": ("S-02",)}
WAVES4 = [["S-01-T"], ["S-01"], ["S-02"], ["S-03"]]


def test_shipped_creates_only_what_live_issues_wait_on(tmp_path):
    """The press a person makes after a spec change: give an issue to every
    unit that work already on the tracker is blocked by, and to nothing else.
    It never extends the frontier, however the waves were renumbered, and
    nobody has to remember how far the last press reached."""
    gh = Tracker()
    ship(tmp_path, gh, order=["S-02"], edges={}, waves=[["S-02"]])
    result = ship(tmp_path, gh, horizon="shipped", order=ORDER4, edges=EDGES4, waves=WAVES4)
    assert [c["key"] for c in result["created"]] == ["S-01-T", "S-01"]
    assert result["held"] == ["S-03"]


def test_shipped_holds_a_new_unit_nothing_out_waits_on(tmp_path):
    """New work beside the shipped region is still new work: it waits for a
    press that opens waves."""
    gh = Tracker()
    ship(tmp_path, gh)
    grown = {**EDGES, "S-01b": ("S-01-T",)}
    result = ship(tmp_path, gh, horizon="shipped", order=["S-01-T", "S-01", "S-01b", "S-02"],
                  edges=grown, waves=[["S-01-T"], ["S-01", "S-01b"], ["S-02"]])
    assert result["created"] == [] and result["held"] == ["S-01b"]


def test_shipped_includes_an_implementation_whose_tests_are_out(tmp_path):
    """Tests on the tracker with no implementation to make them pass are broken
    by definition, whether or not anything waits on that implementation. Only
    the unit and what it waits on come with it -- never what waits on it."""
    gh = Tracker()
    ship(tmp_path, gh, order=["S-01-T"], edges={}, waves=[["S-01-T"]])
    result = ship(tmp_path, gh, horizon="shipped", order=ORDER4, edges=EDGES4, waves=WAVES4)
    assert [c["key"] for c in result["created"]] == ["S-01"]
    assert result["held"] == ["S-02", "S-03"]
    assert gh.issues[2]["blocked_by"] == {1}


def test_shipped_with_nothing_out_creates_nothing(tmp_path):
    gh = Tracker()
    result = ship(tmp_path, gh, horizon="shipped", order=ORDER4, edges=EDGES4, waves=WAVES4)
    assert result["created"] == [] and gh.n == 0
    assert sorted(result["held"]) == sorted(ORDER4)


def test_what_the_horizon_held_back_is_reported(tmp_path):
    gh = Tracker()
    result = ship(tmp_path, gh, horizon=1)
    assert result["held"] == ["S-01", "S-02"]


# -- withdrawal is repo-wide --------------------------------------------------


def test_a_withdrawal_covers_every_cut_and_spares_picked_up_work(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[2]["labels"].append("cto:admitted")
    out = rollback(log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=99,
                   client=gh, now_fn=lambda: 3.0, pickup=PICKUP)
    assert sorted(n for c, n in [x[:2] for x in gh.calls] if c == "close") == [1, 3]
    assert out["skipped"][0]["number"] == 2
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert set(live) == {"S-01"}


def test_the_log_keeps_creation_and_withdrawal_as_two_facts(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    ship(tmp_path, gh, order=["S-01-T"], edges={}, waves=[["S-01-T"]])
    emissions, rollbacks = read_log(tmp_path / "emissions.jsonl")
    assert len(emissions) == 2 and len(rollbacks) == 1
    assert set(rollbacks[0].closed) == {"S-01", "S-02"}


def test_an_edit_moves_our_labels_by_difference(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh, labels=("team:old",))
    ship(tmp_path, gh, bodies={"S-02": "changed"}, labels=("team:new",))
    labels = gh.issues[3]["labels"]
    assert "team:new" in labels and "team:old" not in labels


# -- a superseded anchor keeps its issue ---------------------------------------

# S-02 superseded by S-03: unit S-02 becomes unit S-03, the same work.
RENAMED_ORDER = ["S-01-T", "S-01", "S-03"]
RENAMED_EDGES = {"S-01": ("S-01-T",), "S-03": ("S-01",)}
RENAMED_WAVES = [["S-01-T"], ["S-01"], ["S-03"]]
RENAMED = {"S-03": ("S-02",)}


def ship_renamed(tmp_path, gh, **kw):
    return ship(tmp_path, gh, order=RENAMED_ORDER, edges=RENAMED_EDGES,
                waves=RENAMED_WAVES, renamed=RENAMED, **kw)


def test_a_superseded_anchor_edits_its_predecessors_issue_instead_of_creating(tmp_path):
    """Units are keyed by their spec anchor, so superseding S-02 turns unit
    S-02 into a brand-new S-03. The sync used to withdraw #3 and open a fresh
    issue for the same work -- or, if #3 was picked up, leave it live and open
    a second copy beside it. DARK's S-107 -> S-127 was the first."""
    gh = Tracker()
    ship(tmp_path, gh)
    result = ship_renamed(tmp_path, gh)
    assert result["created"] == [] and result["withdrawn"] == []
    assert [e["key"] for e in result["edited"]] == ["S-03"]
    assert gh.issues[3]["title"].startswith("S-03")
    assert gh.n == 3
    live, wired = standing(tmp_path / "emissions.jsonl", REPO, RENAMED)
    assert live["S-03"]["number"] == 3 and "S-02" not in live
    assert wired == {"S-01<-S-01-T", "S-03<-S-01"}


def test_a_superseded_anchor_whose_issue_was_finished_is_reported_not_duplicated(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    gh.calls.clear()
    result = ship_renamed(tmp_path, gh)
    assert result["created"] == []
    assert [t["key"] for t in result["settled"]] == ["S-03"] and result["touched"] == []
    assert [c for c in gh.writes() if c[0] in ("create", "edit", "close")] == []


def test_a_press_after_the_rename_neither_withdraws_nor_recreates(tmp_path):
    """The log still holds S-02 -> #3 from the first press. Read naively, a
    later press sees S-02 gone from the cut and closes #3 -- the issue S-03
    now owns."""
    gh = Tracker()
    ship(tmp_path, gh)
    ship_renamed(tmp_path, gh)
    gh.calls.clear()
    result = ship_renamed(tmp_path, gh)
    assert gh.writes() == []
    assert result["withdrawn"] == [] and result["created"] == []
    assert gh.issues[3]["state"] == "open"


def test_standing_without_the_rename_map_still_holds_one_key_per_issue(tmp_path):
    """Once an emission records #3 under S-03, the older S-02 -> #3 line is
    history, whether or not the caller knows about the supersession."""
    gh = Tracker()
    ship(tmp_path, gh)
    ship_renamed(tmp_path, gh)
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert sorted(live) == ["S-01", "S-01-T", "S-03"]


# -- the order as GitHub holds it ----------------------------------------------


def _stale(gh):
    """S-02's issue linked to a closed issue of ours under S-01's label -- a
    swept follow-up -- instead of S-01's live issue. The record still says the
    pair is wired, which is exactly what hid this on DARK."""
    gh.n += 1
    gh.issues[gh.n] = {"title": "S-01 (follow-up to #2)", "body": "", "labels": ["mu-spec:S-01"],
                       "state": "closed", "state_reason": "not_planned", "blocked_by": set()}
    gh.issues[3]["blocked_by"] = {gh.n}
    return gh.n


def test_a_link_to_an_issue_the_key_no_longer_lives_on_is_repointed(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    stale = _stale(gh)
    result = ship(tmp_path, gh)
    assert gh.issues[3]["blocked_by"] == {2}
    assert ("unwire", 3, stale) in gh.calls
    assert result["unwired"] == []


def test_a_link_to_someone_elses_issue_is_left_alone(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.n += 1
    gh.issues[gh.n] = {"title": "hand-made", "body": "", "labels": ["bug"],
                       "state": "open", "state_reason": "", "blocked_by": set()}
    gh.issues[3]["blocked_by"].add(gh.n)
    ship(tmp_path, gh)
    assert gh.issues[3]["blocked_by"] == {2, gh.n}


def test_a_picked_up_issues_links_are_never_repaired(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    stale = _stale(gh)
    gh.issues[3]["labels"].append("cto:admitted")
    ship(tmp_path, gh)
    assert gh.issues[3]["blocked_by"] == {stale}
