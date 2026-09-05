from __future__ import annotations

import pytest

from mu_spec.graph import Entry
from mu_spec.identifiers import parse
from mu_spec.storage import ProjectStore


def _project(tmp_path) -> ProjectStore:
    """Two slices at spec level: listings owns S·01 and S·02, payouts owns
    S·03, which depends on S·01."""
    store = ProjectStore(tmp_path)
    store.create_project("m")
    store.append(
        "m",
        [
            Entry(id=parse("S·01"), title="index"),
            Entry(id=parse("S·02"), title="filters"),
        ],
        slice_name="listings",
    )
    store.append(
        "m",
        [Entry(id=parse("S·03"), title="ledger", depends_on=(parse("S·01"),))],
        slice_name="payouts",
    )
    for _ in range(3):
        store.allocate("m", "S")
    return store


# -- the backlink ------------------------------------------------------------


def test_a_module_declares_the_spec_entries_it_implements(tmp_path):
    """Without this the bottom layer floats free of the structure and the
    whole scheme is decorative."""
    store = _project(tmp_path)
    store.set_module("m", "search/index.py", ["S·01"])
    assert store.load_manifest("m").modules == {"search/index.py": {parse("S·01")}}


def test_backlinks_persist(tmp_path):
    store = _project(tmp_path)
    store.set_module("m", "search/index.py", ["S·01"])
    reloaded = ProjectStore(tmp_path).load_manifest("m")
    assert reloaded.implementers(parse("S·01")) == ("search/index.py",)


def test_one_module_may_implement_several_entries(tmp_path):
    store = _project(tmp_path)
    store.set_module("m", "search/index.py", ["S·01", "S·02"])
    manifest = store.load_manifest("m")
    assert manifest.implementers(parse("S·02")) == ("search/index.py",)


def test_several_modules_may_implement_one_entry(tmp_path):
    store = _project(tmp_path)
    store.set_module("m", "search/index.py", ["S·01"])
    store.set_module("m", "search/query.py", ["S·01"])
    assert store.load_manifest("m").implementers(parse("S·01")) == (
        "search/index.py",
        "search/query.py",
    )


def test_setting_a_module_replaces_its_declaration(tmp_path):
    """Not merged. A module that stops implementing something must be able to
    say so, or the write set keeps handing out files nobody needs."""
    store = _project(tmp_path)
    store.set_module("m", "search/index.py", ["S·01", "S·02"])
    store.set_module("m", "search/index.py", ["S·01"])
    assert store.load_manifest("m").implementers(parse("S·02")) == ()


def test_declaring_no_entries_drops_the_module(tmp_path):
    store = _project(tmp_path)
    store.set_module("m", "search/index.py", ["S·01"])
    store.set_module("m", "search/index.py", [])
    assert store.load_manifest("m").modules == {}


def test_a_backlink_to_a_nonexistent_entry_is_refused(tmp_path):
    store = _project(tmp_path)
    with pytest.raises(ValueError):
        store.set_module("m", "search/index.py", ["S·99"])


def test_a_backlink_to_a_layer_above_spec_is_refused(tmp_path):
    """Code implements spec. A module claiming to implement an architecture
    entry has skipped the layer that says how."""
    store = _project(tmp_path)
    with pytest.raises(ValueError):
        store.set_module("m", "search/index.py", ["I·01"])


def test_a_malformed_backlink_is_refused(tmp_path):
    store = _project(tmp_path)
    with pytest.raises(ValueError):
        store.set_module("m", "search/index.py", ["nonsense"])
