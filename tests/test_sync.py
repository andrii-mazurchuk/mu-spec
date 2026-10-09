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

    def issue_state(self, repo, number):
        if number in self.fail_reads:
            raise GitHubError("GitHub answered 502", status=502)
        issue = self.issues[number]
        state = {"state": issue["state"], "state_reason": issue["state_reason"],
                 "labels": list(issue["labels"])}
        if number in self.admit_after_read:
            self.admit_after_read.discard(number)
            issue["labels"].append("cto:admitted")
        return state

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
        return [c for c in self.calls if c[0] != "read"]

    def open_numbers(self):
        return sorted(n for n, i in self.issues.items() if i["state"] == "open")


def ship(tmp_path, tracker, *, order=ORDER, edges=EDGES, waves=WAVES,
         horizon=None, bodies=None, pickup=PICKUP, labels=(), renamed=None):
    bodies = bodies or {}
    return emit(renamed=renamed,
        log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3,
        order=list(order), edges=edges, waves=waves, horizon=horizon,
        fetch=lambda k: payload(k, edges.get(k, ()), bodies.get(k, "the contract")),
        client=tracker, now_fn=lambda: 1.0, pickup=pickup, labels=labels,
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


def test_an_issue_somebody_closed_is_touched_whatever_the_reason(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, bodies={"S-02": "changed"})
    assert ("edit", 3) not in gh.calls
    assert "completed" in result["touched"][0]["reason"]


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
    assert [t["key"] for t in result["touched"]] == ["S-03"]
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
