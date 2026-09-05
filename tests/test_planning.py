from __future__ import annotations

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.planning import audit, plan, spec_diff
from mu_spec.storage import Manifest, Slice


def _graph(*extra: Entry) -> Graph:
    """listings owns S·01 and S·02; payouts owns S·03, which depends on S·01."""
    return Graph(
        [
            Entry(id=parse("S·01"), title="index"),
            Entry(id=parse("S·02"), title="filters"),
            Entry(id=parse("S·03"), title="ledger", depends_on=(parse("S·01"),)),
            *extra,
        ]
    )


def _manifest(**modules) -> Manifest:
    return Manifest(
        project="m",
        slices={
            "listings": Slice(
                name="listings", members={parse("S·01"), parse("S·02")}
            ),
            "payouts": Slice(name="payouts", members={parse("S·03")}),
        },
        modules={
            path.replace("__", "/").replace("_py", ".py"): {
                parse(i) for i in ids.split()
            }
            for path, ids in modules.items()
        },
    )


def _mods() -> Manifest:
    return _manifest(
        search__index_py="S·01",
        search__filters_py="S·02",
        payouts__ledger_py="S·03",
    )


# -- the diff ----------------------------------------------------------------


def test_the_first_iteration_diff_is_the_whole_spec_layer():
    """since=0 -- everything is new, which is the right answer before any
    code exists."""
    diff = spec_diff(_graph(), since=0)
    assert diff.added == (parse("S·01"), parse("S·02"), parse("S·03"))
    assert diff.superseding == ()


def test_nothing_created_since_the_mark_is_an_empty_diff():
    diff = spec_diff(_graph(), since=3)
    assert diff.changed == ()


def test_an_added_entry_appears_as_added():
    graph = _graph(Entry(id=parse("S·04"), title="paging"))
    assert spec_diff(graph, since=3).added == (parse("S·04"),)


def test_a_superseding_entry_names_what_it_retired():
    graph = _graph(
        Entry(id=parse("S·04"), title="index v2", supersedes=parse("S·01"))
    )
    diff = spec_diff(graph, since=3)
    assert diff.superseding == (parse("S·04"),)
    assert diff.retired == (parse("S·01"),)
    assert diff.added == ()


def test_the_diff_needs_no_history_file():
    """Identifiers are allocated in creation order from a counter that only
    moves up, so 'created since N' is 'numbered above N'. No second copy of
    anything that could drift."""
    graph = _graph(Entry(id=parse("S·04"), title="paging"))
    assert spec_diff(graph, since=3).added == (parse("S·04"),)
    assert spec_diff(graph, since=4).added == ()


# -- write set and read set --------------------------------------------------


def test_a_supersession_puts_the_old_entrys_modules_in_the_write_set():
    """search/index.py was written against S·01. S·01 now means something
    else, so that file is what has to change."""
    graph = _graph(
        Entry(id=parse("S·04"), title="index v2", supersedes=parse("S·01"))
    )
    result = plan(_mods(), graph, spec_diff(graph, since=3))
    assert [r["path"] for r in result["write_set"]] == ["search/index.py"]


def test_a_module_that_consumed_the_changed_meaning_is_read_only():
    """payouts/ledger.py implements S·03, which depends on S·01. S·03 did not
    change, so it is context -- not editable."""
    graph = _graph(
        Entry(id=parse("S·04"), title="index v2", supersedes=parse("S·01"))
    )
    result = plan(_mods(), graph, spec_diff(graph, since=3))
    assert [r["path"] for r in result["read_set"]] == ["payouts/ledger.py"]
    assert result["read_set"][0]["slice"] == "payouts"


def test_an_unrelated_module_is_in_neither_set():
    """search/filters.py implements S·02, which nothing in this change
    touches. This is the bound: the executor cannot see it."""
    graph = _graph(
        Entry(id=parse("S·04"), title="index v2", supersedes=parse("S·01"))
    )
    result = plan(_mods(), graph, spec_diff(graph, since=3))
    paths = {r["path"] for r in result["write_set"] + result["read_set"]}
    assert "search/filters.py" not in paths


def test_a_module_implementing_two_changed_entries_is_one_task():
    """Not two. Forty near-identical tickets means the change was
    misclassified."""
    graph = _graph(
        Entry(id=parse("S·04"), title="v2", supersedes=parse("S·01")),
        Entry(id=parse("S·05"), title="v2", supersedes=parse("S·02")),
    )
    manifest = _manifest(search__core_py="S·01 S·02")
    result = plan(manifest, graph, spec_diff(graph, since=3))
    assert len(result["write_set"]) == 1
    assert result["write_set"][0]["implements"] == ["S·01", "S·02"]


def test_an_added_entry_nothing_implements_is_reported_as_new_work():
    """Not a failure -- but it has to be visible, or a planner silently emits
    no task for a requirement that has no file yet."""
    graph = _graph(Entry(id=parse("S·04"), title="paging"))
    result = plan(_mods(), graph, spec_diff(graph, since=3))
    assert result["unimplemented"] == ["S·04"]
    assert result["write_set"] == []


def test_a_module_in_the_write_set_is_never_also_in_the_read_set():
    graph = _graph(
        Entry(id=parse("S·04"), title="v2", supersedes=parse("S·01")),
        Entry(id=parse("S·05"), title="v2", supersedes=parse("S·03")),
    )
    result = plan(_mods(), graph, spec_diff(graph, since=3))
    write = {r["path"] for r in result["write_set"]}
    read = {r["path"] for r in result["read_set"]}
    assert write & read == set()
    assert "payouts/ledger.py" in write


def test_an_empty_diff_plans_nothing():
    result = plan(_mods(), _graph(), spec_diff(_graph(), since=3))
    assert result["write_set"] == []
    assert result["read_set"] == []


# -- the audit ---------------------------------------------------------------


def test_touching_exactly_the_write_set_is_clean():
    result = audit(["search/index.py"], ["search/index.py"])
    assert result["clean"] is True
    assert result["undeclared"] == []


def test_touching_a_file_outside_the_write_set_is_a_failure():
    """Either the planner missed a dependency or the executor freelanced.
    Both are worth knowing."""
    result = audit(
        ["search/index.py", "payouts/ledger.py"], ["search/index.py"]
    )
    assert result["clean"] is False
    assert result["undeclared"] == ["payouts/ledger.py"]


def test_a_declared_file_left_untouched_is_reported_but_not_a_failure():
    result = audit([], ["search/index.py"])
    assert result["clean"] is True
    assert result["declared_untouched"] == ["search/index.py"]


def test_the_audit_never_runs_git():
    """The caller runs git and passes the paths. A unit that shells out is a
    unit that executes, which this one does not do -- the function takes two
    lists of strings and that is the whole interface."""
    assert audit(["a.py"], ["a.py"])["clean"] is True
