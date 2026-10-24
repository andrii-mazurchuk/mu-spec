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
        # Parent number -> the error the next attach under it raises, once.
        self.fail_sub: dict[int, Exception] = {}
        # Whether attaching under a closed parent reopens it. UNVERIFIED on
        # GitHub, so both behaviours are tested.
        self.reopen_on_attach = False
        # A token without push access: GitHub creates the issue and drops
        # every label, silently (M30, DARK m1109-5).
        self.drop_labels = False

    # -- the client surface --
    def create_issue(self, repo, title, body, labels=()):
        self.n += 1
        self.issues[self.n] = {"title": title, "body": body,
                               "labels": list(labels), "state": "open",
                               "state_reason": "", "blocked_by": set()}
        if self.drop_labels:
            self.issues[self.n]["labels"] = []
        self.calls.append(("create", self.n))
        return Issue(id=1000 + self.n, number=self.n, url=f"u/{self.n}",
                     labels=tuple(self.issues[self.n]["labels"]))

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
        self.calls.append(("state", number))
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

    def add_sub_issue(self, repo, parent_number, sub_issue_id):
        self.calls.append(("sub", parent_number, sub_issue_id - 1000))
        error = self.fail_sub.pop(parent_number, None)
        if error is not None:
            raise error
        self.issues[sub_issue_id - 1000]["parent"] = parent_number
        if self.reopen_on_attach:
            self.issues[parent_number].update(state="open", state_reason="reopened")

    # -- what a test reads --
    def writes(self):
        return [c for c in self.calls if c[0] not in ("read", "list", "state")]

    def open_numbers(self):
        return sorted(n for n, i in self.issues.items() if i["state"] == "open")


def ship(tmp_path, tracker, *, order=ORDER, edges=EDGES, waves=WAVES,
         horizon=None, bodies=None, pickup=PICKUP, labels=(), lineage=False, fetch=None, **kw):
    bodies = bodies or {}
    return emit(lineage=lineage,
        log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3,
        order=list(order), edges=edges, waves=waves, horizon=horizon,
        fetch=fetch or (lambda k: payload(k, edges.get(k, ()), bodies.get(k, "the contract"))),
        client=tracker, now_fn=lambda: 1.0, pickup=pickup, labels=labels, project="dark",
        **kw,
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


# -- M26: the write set is part of the contract (V4) ---------------------------


def _with_write_set(paths):
    """A fetch whose S-02 writes `paths` instead of its one default file."""
    def fetch(key):
        unit = payload(key, EDGES.get(key, ()))
        if key == "S-02":
            unit = {**unit, "write_set": list(paths)}
        return unit
    return fetch


def test_a_closed_unit_whose_write_set_grew_gets_a_follow_up(tmp_path):
    """Closed `completed`, contract text untouched, but the unit now owns a file
    it did not when it merged: what merged cannot have written it (M26)."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, fetch=_with_write_set(["S-02.py", "S-02/events.py"]))
    [follow] = result["follow_ups"]
    assert follow["key"] == "S-02" and follow["follow_up_of"] == 3
    assert "S-02/events.py" in gh.issues[follow["number"]]["body"]
    assert gh.issues[3]["state"] == "closed", "never reopened"


def test_a_write_set_listed_in_another_order_is_not_a_change(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh, fetch=_with_write_set(["a.py", "b.py"]))
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, fetch=_with_write_set(["b.py", "a.py"]))
    assert result["follow_ups"] == [] and [s["key"] for s in result["settled"]] == ["S-02"]


def test_a_write_set_follow_up_hangs_under_the_root(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh, lineage=True)
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, lineage=True, fetch=_with_write_set(["S-02.py", "x.py"]))
    [follow] = result["follow_ups"]
    assert ("sub", 3, follow["number"]) in gh.calls


def test_a_v3_record_ignores_the_write_set_so_darks_closed_issues_stay_settled(tmp_path):
    """A record older than V4 holds the contract alone (DARK's: V1, V2, none).
    Reading the write set into it would move every closed unit whose module
    map changed after merge."""
    import json
    from mu_spec.emit import _contract_v3, _V3
    gh = Tracker()
    ship(tmp_path, gh)
    log = tmp_path / "emissions.jsonl"
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    for line in lines:
        for issue in (line.get("issues") or {}).values():
            issue["contract"] = _contract_v3(gh.issues[issue["number"]]["body"])
            assert issue["contract"].startswith(_V3)
    log.write_text("".join(json.dumps(l) + chr(10) for l in lines))
    gh.issues[3].update(state="closed", state_reason="completed")
    moved = ship(tmp_path, gh, fetch=_with_write_set(["S-02.py", "x.py"]))
    assert moved["follow_ups"] == [] and [s["key"] for s in moved["settled"]] == ["S-02"]
    grown = ship(tmp_path, gh, bodies={"S-02": "the contract, grown"})
    assert [f["key"] for f in grown["follow_ups"]] == ["S-02"], "V3 still sees the contract"


def test_a_record_with_no_contract_ignores_the_write_set_too(tmp_path):
    """Most of DARK's records: compared through the closed issue's own text."""
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
    moved = ship(tmp_path, gh, fetch=_with_write_set(["S-02.py", "x.py"]))
    assert moved["follow_ups"] == [] and [s["key"] for s in moved["settled"]] == ["S-02"]


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
ORDER5 = ORDER4 + ["S-04"]
EDGES5 = {**EDGES4, "S-04": ("S-03",)}
WAVES5 = WAVES4 + [["S-04"]]


def test_shipped_creates_only_what_live_issues_wait_on(tmp_path):
    """The press a person makes after a spec change: give an issue to every
    unit that work already on the tracker is blocked by, and to nothing else.
    It never extends the frontier, however the waves were renumbered, and
    nobody has to remember how far the last press reached."""
    gh = Tracker()
    ship(tmp_path, gh, order=["S-02"], edges={}, waves=[["S-02"]])
    result = ship(tmp_path, gh, horizon="shipped", order=ORDER5, edges=EDGES5, waves=WAVES5)
    assert [c["key"] for c in result["created"]] == ["S-01-T", "S-01"]
    assert result["held"] == ["S-03", "S-04"]


def test_shipped_holds_new_work_more_than_one_step_past_the_frontier(tmp_path):
    """New work beside the shipped region is still new work: a chain whose
    leaf waits on a unit with no issue waits for a press that opens waves."""
    gh = Tracker()
    ship(tmp_path, gh)
    grown = {**EDGES, "S-01b": ("S-01-T",), "S-01c": ("S-01b",)}
    result = ship(tmp_path, gh, horizon="shipped",
                  order=["S-01-T", "S-01", "S-01b", "S-02", "S-01c"], edges=grown,
                  waves=[["S-01-T"], ["S-01", "S-01b"], ["S-02", "S-01c"]])
    assert result["created"] == [] and result["held"] == ["S-01b", "S-01c"]


def test_shipped_includes_a_new_leaf_whose_blockers_are_all_out(tmp_path):
    """M29, DARK S-331: the study was the goal of the cut, nothing waits on a
    goal, and two `shipped` presses held it and its tests while everything it
    waits on was out. A leaf one step past the frontier is created, with its
    own test unit."""
    gh = Tracker()
    ship(tmp_path, gh)
    order = ORDER + ["S-09-T", "S-09"]
    edges = {**EDGES, "S-09": ("S-02", "S-09-T")}
    result = ship(tmp_path, gh, horizon="shipped", order=order, edges=edges,
                  waves=WAVES + [["S-09-T"], ["S-09"]])
    assert [c["key"] for c in result["created"]] == ["S-09-T", "S-09"]
    assert result["held"] == []


def test_shipped_includes_the_owner_of_a_module_tests_on_the_tracker_import(tmp_path):
    """M29, DARK S-229 / S-134: merged tests imported modules whose owners
    nothing out waited on, so main stayed red. `uses` is test unit -> the
    units owning what its scenarios import; the owner comes with what it
    waits on, never with what waits on it."""
    gh = Tracker()
    ship(tmp_path, gh)
    order = ORDER + ["S-05", "S-07", "S-06"]
    edges = {**EDGES, "S-06": ("S-05", "S-07")}
    waves = WAVES + [["S-05", "S-07"], ["S-06"]]
    held = ship(tmp_path, gh, horizon="shipped", order=order, edges=edges, waves=waves)
    assert held["created"] == [] and held["held"] == ["S-05", "S-07", "S-06"]
    result = ship(tmp_path, gh, horizon="shipped", order=order, edges=edges, waves=waves,
                  uses={"S-01-T": ("S-05",)})
    assert [c["key"] for c in result["created"]] == ["S-05"]
    assert result["held"] == ["S-07", "S-06"]


def test_the_shipped_horizon_is_one_function_the_preview_shares(tmp_path):
    """The pane's held count is what the press would hold, from the same code."""
    from mu_spec.emit import shipped_horizon
    gh = Tracker()
    ship(tmp_path, gh, order=["S-02"], edges={}, waves=[["S-02"]])
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert shipped_horizon(ORDER5, EDGES5, live) == {"S-01-T", "S-01"}
    assert shipped_horizon(ORDER5, EDGES5, {}) == set()


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


def test_a_later_record_holding_an_issue_under_a_new_key_retires_the_old_line(tmp_path):
    """DARK's log moved #3 from S-02 to S-03 while units were keyed by their
    live anchor. Read naively, a later press would see S-02 gone from the cut
    and close the issue S-03 owns."""
    import json
    gh = Tracker()
    ship(tmp_path, gh)
    log = tmp_path / "emissions.jsonl"
    line = json.loads(log.read_text().splitlines()[-1])
    line["seq"] += 1
    line["issues"]["S-03"] = line["issues"].pop("S-02")
    log.write_text(log.read_text() + json.dumps(line) + "\n")
    live, _ = standing(log, REPO)
    assert sorted(live) == ["S-01", "S-01-T", "S-03"]


# -- lineage keys (M13, D1): one tree of issues per unit ------------------------

# S-02 is keyed by its lineage root while its anchor moves S-02 -> S-05 -> S-07.
CHAIN = {"S-02": ["S-02"], "S-05": ["S-02", "S-05"], "S-07": ["S-02", "S-05", "S-07"]}


def lineage_payload(key, anchor, body):
    unit = payload(key, EDGES.get(key, ()), body)
    unit["unit"] = {**unit["unit"], "anchor": anchor, "entries": [anchor]}
    unit["entries"] = [{"id": anchor, "title": "S-02 title", "body": body,
                        **({"was": CHAIN[anchor][-2::-1]} if anchor != "S-02" else {})}]
    unit["lineage"] = CHAIN[anchor]
    return unit


def ship_lineage(tmp_path, gh, anchor="S-02", body="the contract"):
    return ship(tmp_path, gh, lineage=True, fetch=lambda k: (
        lineage_payload(k, anchor, body) if k == "S-02" else payload(k, EDGES.get(k, ()))))


def _merged_then_grown(tmp_path, gh):
    """Root #3 created, merged, and its contract grown under the live S-05."""
    ship_lineage(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    return ship_lineage(tmp_path, gh, anchor="S-05", body="grown")


def test_a_lineage_press_creates_the_root_and_its_record_says_lineage(tmp_path):
    import json
    from mu_spec.emit import keyed_by_lineage
    gh = Tracker()
    ship_lineage(tmp_path, gh)
    log = tmp_path / "emissions.jsonl"
    assert gh.issues[3]["title"] == "S-02 — S-02.py"
    assert json.loads(log.read_text().splitlines()[-1])["keys"] == "lineage"
    assert keyed_by_lineage(log) is True


def test_a_record_without_the_lineage_marker_keeps_its_project_on_the_old_keys(tmp_path):
    """DARK: every record predates lineage keys. Decided from the record, never
    from a flag somebody sets -- and a project with no record yet starts on them."""
    from mu_spec.emit import keyed_by_lineage
    assert keyed_by_lineage(tmp_path / "emissions.jsonl") is True
    ship(tmp_path, Tracker())
    assert keyed_by_lineage(tmp_path / "emissions.jsonl") is False


def test_an_open_root_follows_its_live_entry_in_place(tmp_path):
    """Nobody built S-02 yet, so its issue is edited, not followed up: the live
    id leads the title and the body names the chain."""
    gh = Tracker()
    ship_lineage(tmp_path, gh)
    result = ship_lineage(tmp_path, gh, anchor="S-05", body="changed")
    assert [e["key"] for e in result["edited"]] == ["S-02"] and gh.n == 3
    assert gh.issues[3]["title"] == "S-05 (rev of S-02) — S-02.py"
    assert "live entry `S-05`. Lineage: `S-02` → `S-05`." in gh.issues[3]["body"]
    assert not [c for c in gh.calls if c[0] == "sub"]


def test_a_contract_change_after_close_is_a_sub_issue_under_the_root(tmp_path):
    gh = Tracker()
    result = _merged_then_grown(tmp_path, gh)
    [follow] = result["follow_ups"]
    assert follow["number"] == 4
    assert gh.issues[4]["title"] == "S-05 (rev of S-02) — S-02.py (follow-up to #3)"
    assert "live entry `S-05`" in gh.issues[4]["body"]
    assert ("sub", 3, 4) in gh.calls and gh.issues[4]["parent"] == 3
    assert result["linked"] == [{"key": "S-02", "number": 4, "parent": 3}]
    assert gh.issues[3]["state"] == "closed" and gh.issues[3]["state_reason"] == "completed"
    assert ("edit", 3) not in gh.calls, "a closed issue is never written"
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert live["S-02"]["root"]["number"] == 3 and live["S-02"]["linked"] is True


def test_an_unmoved_lineage_press_makes_no_new_calls(tmp_path):
    gh = Tracker()
    _merged_then_grown(tmp_path, gh)
    gh.calls.clear()
    result = ship_lineage(tmp_path, gh, anchor="S-05", body="grown")
    # Only the reads every press makes: the open list, and the open issues'
    # native blocked_by. Nothing re-attached, no closed issue re-read.
    assert gh.writes() == []
    assert {c[0] for c in gh.calls} == {"list", "read"}
    assert result["linked"] == [] and result["follow_ups"] == []


def test_every_later_follow_up_hangs_under_the_root(tmp_path):
    gh = Tracker()
    _merged_then_grown(tmp_path, gh)
    gh.issues[4].update(state="closed", state_reason="completed")
    result = ship_lineage(tmp_path, gh, anchor="S-07", body="grown again")
    assert [f["number"] for f in result["follow_ups"]] == [5]
    assert gh.issues[5]["title"] == "S-07 (rev of S-02) — S-02.py (follow-up to #4)"
    assert ("sub", 3, 5) in gh.calls
    assert gh.issues[3]["state"] == gh.issues[4]["state"] == "closed"


def test_an_attach_github_already_holds_counts_as_linked(tmp_path):
    gh = Tracker()
    gh.fail_sub[3] = GitHubError("GitHub answered 422: Validation Failed | Issue may "
                                 "not contain duplicate sub-issues", status=422)
    result = _merged_then_grown(tmp_path, gh)
    assert result["unlinked"] == [] and len(result["linked"]) == 1
    gh.calls.clear()
    ship_lineage(tmp_path, gh, anchor="S-05", body="grown")
    assert not [c for c in gh.calls if c[0] == "sub"]


def test_a_failed_attach_is_retried_next_press_and_never_duplicates_the_follow_up(tmp_path):
    gh = Tracker()
    gh.fail_sub[3] = GitHubError("GitHub answered 502: bad gateway", status=502)
    result = _merged_then_grown(tmp_path, gh)
    assert [u["number"] for u in result["unlinked"]] == [4] and gh.n == 4
    assert "retried next press" in result["unlinked"][0]["reason"]
    again = ship_lineage(tmp_path, gh, anchor="S-05", body="grown")
    assert again["created"] == [] and again["follow_ups"] == [] and gh.n == 4
    assert [l["number"] for l in again["linked"]] == [4] and gh.issues[4]["parent"] == 3


def test_a_parent_found_open_after_the_attach_is_reported_and_never_closed(tmp_path):
    """Unverified whether GitHub reopens a closed parent on attach. If it does,
    the press says so and closes nothing: the root's `completed` is not ours."""
    gh = Tracker()
    gh.reopen_on_attach = True
    result = _merged_then_grown(tmp_path, gh)
    assert [r["number"] for r in result["raced"]] == [3]
    again = ship_lineage(tmp_path, gh, anchor="S-05", body="grown")
    assert again["swept"] == [] and ("close", 3) not in gh.calls
    assert gh.issues[3]["state"] == "open"


def test_a_follow_up_on_the_old_keys_is_never_attached(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    result = ship(tmp_path, gh, bodies={"S-02": "grown"})
    assert [f["number"] for f in result["follow_ups"]] == [4]
    assert gh.issues[4]["title"] == "S-02 — S-02.py (follow-up to #3)"
    assert not [c for c in gh.calls if c[0] == "sub"] and result["linked"] == []


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


# -- after the press: every order names a key a live issue carries -------------


def test_a_blocker_whose_only_issue_was_closed_not_planned_is_reported_dead(tmp_path):
    """DARK m1205-3 / m1206-1: keys whose only issue was closed `not_planned`
    deadlocked ~30 units, and the press reported nothing -- the link pointed
    where the record said, so it looked wired. A consumer treats a blocker
    closed `not_planned` as never satisfied."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[2].update(state="closed", state_reason="not_planned")      # S-01
    result = ship(tmp_path, gh)
    assert [(d["blocked"], d["blocker"]) for d in result["dead"]] == [("S-02", "S-01")]
    assert result["counts"]["dead"] == 1


def test_a_blocker_closed_under_a_key_the_dependent_does_not_name_is_reported_dead(tmp_path):
    """DARK s03-12 / m1206-5: #503 carried S-260-T, #643 waited on S-273-T
    with no `blocked_by_was` naming the old key -- a key no issue carried."""
    import json
    gh = Tracker()

    def press(was):
        return emit(log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3,
                    order=list(ORDER), edges=EDGES, waves=WAVES, horizon=None,
                    fetch=lambda k: {**payload(k, EDGES.get(k, ())),
                                     **({"follows_was": was} if k == "S-02" else {})},
                    client=gh, now_fn=lambda: 1.0, pickup=PICKUP, project="dark")

    press({})
    body = gh.issues[2]["body"]
    assert '"key": "S-01"' in body
    gh.issues[2].update(state="closed", state_reason="completed",
                        body=body.replace('"key": "S-01"', '"key": "S-00"'))
    assert [d["blocker"] for d in press({})["dead"]] == ["S-01"]
    assert press({"S-01": ["S-00"]})["dead"] == []


def test_a_sound_press_reports_nothing_dead(tmp_path):
    gh = Tracker()
    assert ship(tmp_path, gh)["dead"] == []
    gh.issues[1].update(state="closed", state_reason="completed")        # S-01-T merged
    assert ship(tmp_path, gh)["dead"] == []


# -- a follow-up states what changed since the issue it follows -----------------


def _scenarios(tmp_path, gh, entries):
    """S-01-T holding the scenarios given as (id, body, was)."""
    def fetch(key):
        unit = payload(key, EDGES.get(key, ()))
        if key == "S-01-T":
            unit["entries"] = [{"id": i, "title": f"{i} title", "body": b,
                                **({"was": list(w)} if w else {})} for i, b, w in entries]
        return unit
    return emit(log_path=tmp_path / "emissions.jsonl", repo=REPO, cut_seq=3,
                order=list(ORDER), edges=EDGES, waves=WAVES, horizon=None,
                fetch=fetch, client=gh, now_fn=lambda: 1.0, pickup=PICKUP, project="dark")


def _changed(body):
    head, found, rest = body.partition("## Changed since #")
    assert found, "a follow-up carries its delta"
    assert "## Contract" not in head, "the delta comes before the contract"
    return rest.split("\n## Contract", 1)[0]


def test_a_follow_up_names_the_one_entry_added_since_the_merged_issue(tmp_path):
    """DARK u0707-6 / u0810-4: S-286-T and S-309-T re-rendered every scenario
    with one new, and the CTO diffed old issue bodies by hand to find it."""
    gh = Tracker()
    _scenarios(tmp_path, gh, [("T-01", "first case", ()), ("T-02", "second case", ())])
    gh.issues[1].update(state="closed", state_reason="completed")
    result = _scenarios(tmp_path, gh, [("T-01", "first case", ()), ("T-02", "second case", ()),
                                       ("T-03", "third case", ())])
    [follow] = result["follow_ups"]
    delta = _changed(gh.issues[follow["number"]]["body"])
    assert delta.startswith("1")
    assert "`T-03` — added" in delta
    assert "T-01" not in delta and "T-02" not in delta


def test_a_follow_up_says_which_ids_were_renumbered_and_which_left(tmp_path):
    """DARK u0701-5 / u0904-8: T-346 became T-689 with the same text, and
    implementers matched renumbered scenarios by guesswork."""
    gh = Tracker()
    _scenarios(tmp_path, gh, [("T-01", "first case", ()), ("T-02", "second case", ())])
    gh.issues[1].update(state="closed", state_reason="completed")
    result = _scenarios(tmp_path, gh, [("T-04", "second case", ("T-02",)),
                                       ("T-03", "third case", ())])
    delta = _changed(gh.issues[result["follow_ups"][0]["number"]]["body"])
    assert "`T-04` — renumbered from `T-02`, text unchanged" in delta
    assert "`T-03` — added" in delta
    assert "`T-01` — removed" in delta


def test_a_follow_up_shows_the_changed_text_of_an_entry(tmp_path):
    """DARK u0501-4: the follow-up never said what in the contract moved."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[2].update(state="closed", state_reason="completed")       # S-01
    result = ship(tmp_path, gh, bodies={"S-01": "a changed contract"})
    delta = _changed(gh.issues[result["follow_ups"][0]["number"]]["body"])
    assert "`S-01` — changed" in delta
    assert "-the contract" in delta and "+a changed contract" in delta


def test_an_edited_follow_up_still_states_its_delta_against_the_merged_issue(tmp_path):
    gh = Tracker()
    _scenarios(tmp_path, gh, [("T-01", "first case", ())])
    gh.issues[1].update(state="closed", state_reason="completed")
    first = _scenarios(tmp_path, gh, [("T-01", "first case", ()), ("T-02", "second", ())])
    number = first["follow_ups"][0]["number"]
    _scenarios(tmp_path, gh, [("T-01", "first case", ()), ("T-02", "second", ()),
                              ("T-03", "third", ())])
    delta = _changed(gh.issues[number]["body"])
    assert "`T-02` — added" in delta and "`T-03` — added" in delta


# -- a settled issue is read once, not every press ----------------------------


def _all_settled(tmp_path, gh):
    ship(tmp_path, gh)
    for issue in gh.issues.values():
        issue.update(state="closed", state_reason="completed")
    return ship(tmp_path, gh, labels=("team:x",))


def test_an_unmoved_press_after_settling_makes_only_the_open_list_call(tmp_path):
    """DARK: 193 closed issues re-read on every press, ~5 minutes for a press
    that changes nothing (docs/TICKETS.md §3: an unmoved cut costs no reads)."""
    gh = Tracker()
    first = _all_settled(tmp_path, gh)
    assert sorted(t["key"] for t in first["settled"]) == ["S-01", "S-01-T", "S-02"]
    gh.calls.clear()
    again = ship(tmp_path, gh, labels=("team:x",))
    assert gh.calls == [("list", 0)]
    assert sorted(t["key"] for t in again["settled"]) == ["S-01", "S-01-T", "S-02"]
    assert all("completed" in t["reason"] for t in again["settled"])


def test_a_settled_marker_is_keyed_on_the_issue_number(tmp_path):
    gh = Tracker()
    _all_settled(tmp_path, gh)
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert live["S-02"]["settled"]["number"] == 3
    assert live["S-02"]["settled"]["state_reason"] == "completed"
    assert live["S-02"]["settled"]["key"] == "S-02"


def test_a_settled_issue_gone_from_the_cut_is_not_read_again(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[3].update(state="closed", state_reason="completed")
    cut = dict(order=["S-01-T", "S-01"], edges={"S-01": ("S-01-T",)},
               waves=[["S-01-T"], ["S-01"]])
    ship(tmp_path, gh, **cut)
    gh.calls.clear()
    again = ship(tmp_path, gh, **cut)
    assert ("state", 3) not in gh.calls
    assert [t["key"] for t in again["settled"]] == ["S-02"]


def test_a_settled_issue_reopened_by_hand_is_read_and_edited(tmp_path):
    gh = Tracker()
    _all_settled(tmp_path, gh)
    gh.issues[3].update(state="open", state_reason="reopened")
    result = ship(tmp_path, gh, labels=("team:x",))
    assert ("state", 3) in gh.calls and ("edit", 3) in gh.calls
    assert [e["key"] for e in result["edited"]] == ["S-02"]
    live, _ = standing(tmp_path / "emissions.jsonl", REPO)
    assert "settled" not in live["S-02"]


def test_a_settled_issue_whose_contract_moved_is_read_and_followed_up(tmp_path):
    gh = Tracker()
    _all_settled(tmp_path, gh)
    gh.calls.clear()
    result = ship(tmp_path, gh, labels=("team:x",), bodies={"S-02": "the contract, grown"})
    assert ("state", 3) in gh.calls
    assert [f["key"] for f in result["follow_ups"]] == ["S-02"]


def test_a_settled_blocker_of_an_open_issue_is_still_verified(tmp_path):
    """The dead-blocker check reads a closed blocker every press; the marker
    skips only the settle read."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[2].update(state="closed", state_reason="completed")
    ship(tmp_path, gh, labels=("team:x",))
    gh.calls.clear()
    result = ship(tmp_path, gh, labels=("team:x",))
    assert [t["key"] for t in result["settled"]] == ["S-01"]
    assert gh.calls.count(("state", 2)) == 1, "verify's read, and only that"
    assert result["dead"] == []
    gh.issues[2]["state_reason"] = "not_planned"
    result = ship(tmp_path, gh, labels=("team:x",))
    assert [d["blocker"] for d in result["dead"]] == ["S-01"]


# -- M31 (D11): merge order, derived at render time, never an entry edge -------
#
# Two DARK cases, both a TEST follow-up of a unit D and an IMPLEMENTATION
# follow-up of a unit X that D waits on, transitively, both open:
# - m1210-3: S-325's follow-up (#852) was out; the tests of S-308/S-309/S-330
#   (which wait on S-325) were restated after it (#876, #877, #878) to pass
#   before and after S-325, and had to merge FIRST or 21 tests went red.
# - m1206-17: S-271-T's test follow-up (#645) was out; S-217's storage fix
#   followed (the #374 follow-up, iss-0222), and #645 had to wait for IT.
# In both the later-opened follow-up merges first: it is the repair.

M_ORDER = ["S-01-T", "S-01", "S-02-T", "S-02"]
M_EDGES = {"S-01": ("S-01-T",), "S-02": ("S-02-T", "S-01")}
M_WAVES = [["S-01-T"], ["S-01"], ["S-02-T"], ["S-02"]]


def _mship(tmp_path, gh, bodies=None, edges=M_EDGES):
    return ship(tmp_path, gh, order=M_ORDER, edges=edges, waves=M_WAVES, bodies=bodies or {})


def _block(gh, n):
    import json
    return json.loads(gh.issues[n]["body"].split("```json")[-1].split("```")[0])


def _two_merged(tmp_path, gh):
    """#1..#4 out; S-01 (#2) and S-02-T (#3) merged."""
    _mship(tmp_path, gh)
    gh.issues[2].update(state="closed", state_reason="completed")
    gh.issues[3].update(state="closed", state_reason="completed")


def test_no_follow_up_means_no_merge_order_and_no_new_field(tmp_path):
    gh = Tracker()
    result = _mship(tmp_path, gh)
    assert all("order" not in _block(gh, n) for n in gh.issues)
    assert result["merge_order"] == []


def test_a_restated_test_merges_before_the_older_follow_up_it_guards(tmp_path):
    """m1210-3: X's follow-up is out; D's test is restated after it. X waits,
    and the press that opens the restated test is the one that says so."""
    gh = Tracker()
    _two_merged(tmp_path, gh)
    [x] = _mship(tmp_path, gh, bodies={"S-01": "S-01 grown"})["follow_ups"]
    bodies = {"S-01": "S-01 grown", "S-02-T": "restated"}
    second = _mship(tmp_path, gh, bodies=bodies)
    [t] = second["follow_ups"]
    assert (x["number"], t["number"]) == (5, 6)
    assert 6 in gh.issues[5]["blocked_by"], "GitHub sees it"
    block = _block(gh, 5)
    assert "S-02-T" in block["blocked_by"] and block["order"] == {"S-02-T": "merge"}
    assert "order" not in _block(gh, 6) and 5 not in gh.issues[6]["blocked_by"]
    assert second["merge_order"] == [{"blocked": "S-01", "blocker": "S-02-T", "order": "merge"}]
    gh.calls.clear()
    _mship(tmp_path, gh, bodies=bodies)
    assert gh.writes() == [], "settled in the press that derived it"


def test_a_test_follow_up_waits_for_the_newer_fix_it_needs(tmp_path):
    """m1206-17: D's test follow-up is out; X's fix follows it. The test waits."""
    gh = Tracker()
    _two_merged(tmp_path, gh)
    _mship(tmp_path, gh, bodies={"S-02-T": "restated"})                       # #5
    result = _mship(tmp_path, gh, bodies={"S-02-T": "restated", "S-01": "fixed"})  # #6
    assert 6 in gh.issues[5]["blocked_by"]
    assert _block(gh, 5)["order"] == {"S-01": "merge"}
    assert result["merge_order"] == [{"blocked": "S-02-T", "blocker": "S-01", "order": "merge"}]


def test_a_merge_order_leaves_when_its_blocker_merges(tmp_path):
    gh = Tracker()
    _two_merged(tmp_path, gh)
    _mship(tmp_path, gh, bodies={"S-01": "S-01 grown"})
    bodies = {"S-01": "S-01 grown", "S-02-T": "restated"}
    _mship(tmp_path, gh, bodies=bodies)
    gh.issues[6].update(state="closed", state_reason="completed")
    result = _mship(tmp_path, gh, bodies=bodies)
    assert "order" not in _block(gh, 5) and 6 not in gh.issues[5]["blocked_by"]
    assert result["merge_order"] == []


def test_a_merge_order_is_never_an_entry_edge(tmp_path):
    """Derived per press from the record: the edges handed in are untouched."""
    gh = Tracker()
    edges = {k: tuple(v) for k, v in M_EDGES.items()}
    _two_merged(tmp_path, gh)
    _mship(tmp_path, gh, bodies={"S-01": "g"}, edges=edges)
    _mship(tmp_path, gh, bodies={"S-01": "g", "S-02-T": "r"}, edges=edges)
    assert edges == M_EDGES


def test_merge_orders_that_close_a_loop_are_dropped_and_reported_never_refused(tmp_path):
    """S-02 waits on S-01; S-03 on S-02. Open follow-ups S-01 #1 < S-03-T #2 <
    S-02 #3: S-01 waits on S-03-T, S-03-T on S-02, S-02 on S-01. The two merge
    orders in that loop go; the press goes on (ratified 2026-10-24)."""
    import json
    order = ["S-01", "S-03-T", "S-02", "S-03"]
    edges = {"S-02": ("S-01",), "S-03": ("S-03-T", "S-02")}
    waves = [["S-01"], ["S-03-T"], ["S-02"], ["S-03"]]
    gh = Tracker()
    ship(tmp_path, gh, order=order, edges=edges, waves=waves)
    log = tmp_path / "emissions.jsonl"
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    for key in ("S-01", "S-03-T", "S-02"):
        lines[-1]["issues"][key]["follow_up_of"] = 90
    log.write_text("".join(json.dumps(l) + chr(10) for l in lines))
    gh.calls.clear()
    result = ship(tmp_path, gh, order=order, edges=edges, waves=waves,
                  bodies={"S-03": "changed"})
    assert result["emitted"] is True and "refused" not in result
    assert result["dropped_orders"] == [{
        "loop": ["S-01", "S-03-T", "S-02", "S-01"],
        "orders": [{"blocked": "S-01", "blocker": "S-03-T"},
                   {"blocked": "S-03-T", "blocker": "S-02"}]}]
    assert result["counts"]["dropped_orders"] == 2 and result["merge_order"] == []
    assert [e["key"] for e in result["edited"]] == ["S-03"], "the press went on"
    assert all("order" not in _block(gh, n) for n in gh.issues)


def test_only_the_orders_inside_the_loop_are_dropped(tmp_path):
    from mu_spec.emit import prune_merge_cycles
    edges = {"S-02": ("S-01",), "S-03": ("S-03-T", "S-02")}
    kept, dropped = prune_merge_cycles(
        edges, {"S-01": ("S-03-T",), "S-03-T": ("S-02",), "S-09": ("S-03-T",)})
    assert kept == {"S-09": ("S-03-T",)}
    assert [len(d["orders"]) for d in dropped] == [2]


def test_a_loop_in_the_units_own_edges_still_refuses_before_any_write(tmp_path):
    """The guard: gates make this impossible, and merge order cannot cause it."""
    order = ["S-01", "S-02"]
    edges = {"S-01": ("S-02",), "S-02": ("S-01",)}
    gh = Tracker()
    result = ship(tmp_path, gh, order=order, edges=edges, waves=[order])
    assert result["emitted"] is False and gh.writes() == []
    assert result["refused"] == [{"cycle": ["S-01", "S-02", "S-01"]}]
    assert "S-01 -> S-02 -> S-01" in result["reason"]


# -- M25: a renumber is not a contract change -----------------------------------


def test_a_renumbered_scenario_that_moves_to_the_end_opens_no_follow_up(tmp_path):
    """DARK S-260-T / S-263-T / S-315-T: T-347 became T-669 with the same text,
    and because a unit lists its scenarios in id order the renamed one moved
    to the end -- the masked fingerprint moved with it and a full follow-up
    unit was opened for nothing. Entries are compared as a set."""
    gh = Tracker()
    _scenarios(tmp_path, gh, [("T-01", "first", ()), ("T-02", "second", ()), ("T-03", "third", ())])
    gh.issues[1].update(state="closed", state_reason="completed")
    result = _scenarios(tmp_path, gh, [("T-01", "first", ()), ("T-03", "third", ()),
                                       ("T-04", "second", ("T-02",))])
    assert result["follow_ups"] == []
    assert "S-01-T" in [s["key"] for s in result["settled"]]


def test_a_renumber_beside_a_real_change_still_follows_up(tmp_path):
    gh = Tracker()
    _scenarios(tmp_path, gh, [("T-01", "first", ()), ("T-02", "second", ())])
    gh.issues[1].update(state="closed", state_reason="completed")
    result = _scenarios(tmp_path, gh, [("T-01", "first, now stricter", ()),
                                       ("T-04", "second", ("T-02",))])
    assert [f["key"] for f in result["follow_ups"]] == ["S-01-T"]


def test_a_v4_record_renumbered_is_compared_through_the_closed_issue(tmp_path):
    """Records written before V5 hold an order-sensitive hash; the closed
    issue's own text settles a renumber without a follow-up."""
    import json
    from mu_spec.emit import _contract_v4
    gh = Tracker()
    _scenarios(tmp_path, gh, [("T-01", "first", ()), ("T-02", "second", ()), ("T-03", "third", ())])
    log = tmp_path / "emissions.jsonl"
    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        for issue in line["issues"].values():
            issue["contract"] = _contract_v4(gh.issues[issue["number"]]["body"])
    log.write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")
    gh.issues[1].update(state="closed", state_reason="completed")
    same = _scenarios(tmp_path, gh, [("T-01", "first", ()), ("T-03", "third", ()),
                                     ("T-04", "second", ("T-02",))])
    assert same["follow_ups"] == []
    moved = _scenarios(tmp_path, gh, [("T-01", "first", ()), ("T-03", "third, changed", ()),
                                      ("T-04", "second", ("T-02",))])
    assert [f["key"] for f in moved["follow_ups"]] == ["S-01-T"]


# -- T-623: the implementation follows its tests ---------------------------------


def test_tests_restated_after_the_implementation_merged_follow_up_the_implementation(tmp_path):
    """DARK T-623: S-262 merged, then S-262-T's follow-ups redefined the
    tests while S-262's own contract and write set stayed the same, and the
    test sat red on main with no owner. The signal is mechanical: the test
    unit's contract moved in a press where the implementation is closed
    `completed`."""
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[1].update(state="closed", state_reason="completed")   # S-01-T
    gh.issues[2].update(state="closed", state_reason="completed")   # S-01
    result = ship(tmp_path, gh, bodies={"S-01-T": "the contract, restated"})
    assert [(f["key"], f["follow_up_of"]) for f in result["follow_ups"]] == [
        ("S-01-T", 1), ("S-01", 2)]
    test_fu, impl_fu = (f["number"] for f in result["follow_ups"])
    body = gh.issues[impl_fu]["body"]
    assert "(follow-up to #2)" in gh.issues[impl_fu]["title"]
    assert f"#{test_fu}" in body and "already satisfied" in body
    assert "## Changed since" not in body, "its own contract did not change"
    again = ship(tmp_path, gh, bodies={"S-01-T": "the contract, restated"})
    assert again["follow_ups"] == [] and again["created"] == []


def test_an_open_implementation_is_left_alone_when_its_tests_follow_up(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[1].update(state="closed", state_reason="completed")   # S-01-T only
    result = ship(tmp_path, gh, bodies={"S-01-T": "the contract, restated"})
    assert [f["key"] for f in result["follow_ups"]] == ["S-01-T"]
    assert ("edit", 2) not in gh.calls


def test_an_implementation_withdrawn_is_not_followed_up_for_its_tests(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.issues[1].update(state="closed", state_reason="completed")
    gh.issues[2].update(state="closed", state_reason="not_planned")
    result = ship(tmp_path, gh, bodies={"S-01-T": "the contract, restated"})
    assert [f["key"] for f in result["follow_ups"]] == ["S-01-T"]


# -- M30: one unit, and the labels GitHub kept --------------------------------------


def test_one_unit_mode_creates_only_that_unit_and_touches_nothing_else(tmp_path):
    """DARK p09-1 / m1201-8: two 144-issue batches shipped and withdrawn before
    one ticket was seen to ingest. A press of one unit proves the loop."""
    gh = Tracker()
    result = ship(tmp_path, gh, only="S-01-T")
    assert [c["key"] for c in result["created"]] == ["S-01-T"]
    assert result["held"] == ["S-01", "S-02"]
    gh.calls.clear()
    edited = ship(tmp_path, gh, only="S-01-T", bodies={"S-01-T": "changed"},
                  order=["S-01-T", "S-01"])
    assert [e["key"] for e in edited["edited"]] == ["S-01-T"]
    assert not [c for c in gh.writes() if c[0] in ("create", "close")]


def test_one_unit_mode_never_withdraws_or_edits_another_unit(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh)
    gh.calls.clear()
    result = ship(tmp_path, gh, only="S-01", order=["S-01-T", "S-01"],
                  bodies={"S-01-T": "changed"})
    assert gh.writes() == [] and result["withdrawn"] == [] and result["edited"] == []
    assert result["swept"] == []


def test_one_unit_mode_wires_the_orders_that_touch_it(tmp_path):
    gh = Tracker()
    ship(tmp_path, gh, horizon=1)
    result = ship(tmp_path, gh, only="S-01")
    assert [c["key"] for c in result["created"]] == ["S-01"]
    assert gh.issues[2]["blocked_by"] == {1}
    assert result["unwired"] == []


def test_one_unit_mode_refuses_a_key_not_in_the_cut(tmp_path):
    gh = Tracker()
    result = ship(tmp_path, gh, only="S-99")
    assert result["emitted"] is False and "S-99" in result["reason"] and gh.calls == []


def test_labels_github_dropped_on_create_are_reported(tmp_path):
    """DARK m1109-5: a token without push access gets every issue created and
    every label dropped, silently. Compared on the create's own response."""
    gh = Tracker()
    gh.drop_labels = True
    result = ship(tmp_path, gh, only="S-01-T", labels=("team:x",))
    [bare] = result["unlabelled"]
    assert bare["key"] == "S-01-T" and "team:x" in bare["missing"]
    assert result["counts"]["unlabelled"] == 1
    gh.drop_labels = False
    fine = ship(tmp_path, gh, only="S-01", labels=("team:x",))
    assert fine["unlabelled"] == []
