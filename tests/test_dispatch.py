from __future__ import annotations

from mu_spec.dispatch import (
    BACK_CHECK,
    BUILD,
    DERIVATION,
    REPAIR,
    SLICING,
    TRIAGE,
    select,
)
from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import parse
from mu_spec.reconcile import Batch
from mu_spec.storage import Manifest, Slice


def _e(ident: str, derives_from: str = "", depends_on: str = "") -> Entry:
    return Entry(
        id=parse(ident),
        derives_from=tuple(parse(d) for d in derives_from.split() if d),
        depends_on=tuple(parse(d) for d in depends_on.split() if d),
        title=ident,
    )


def _manifest(modules: dict | None = None, **slices) -> Manifest:
    return Manifest(
        project="m",
        slices={
            name: Slice(
                name=name,
                members={parse(i) for i in spec[0].split()},
                type=spec[1] if len(spec) > 1 else "slice",
            )
            for name, spec in slices.items()
        },
        modules={
            path: {parse(i) for i in ids.split()}
            for path, ids in (modules or {}).items()
        },
    )


def _complete() -> tuple[Manifest, Graph]:
    """One slice, fully derived I -> B -> A -> S, with the spec entry
    implemented by a module. Nothing is eligible in this state."""
    graph = Graph(
        [
            _e("I·01"),
            _e("B·01", "I·01"),
            _e("A·01", "B·01"),
            _e("S·01", "A·01"),
        ]
    )
    manifest = _manifest(
        {"app/thing.py": "S·01"},
        listings=("B·01 A·01 S·01",),
    )
    return manifest, graph


# -- the floor ---------------------------------------------------------------


def test_a_finished_project_dispatches_nothing():
    """Exit 0, run nothing -- the ladder must have a floor, or the loop
    launches a session to discover there is no work."""
    manifest, graph = _complete()
    assert select(manifest, graph) is None


# -- the rungs, each in isolation --------------------------------------------


def test_an_unchecked_correction_dispatches_back_check():
    manifest, graph = _complete()
    got = select(manifest, graph, unchecked=("msg-0007",))
    assert got is not None
    assert got.session_type == BACK_CHECK
    assert got.scope["request"] == "msg-0007"


def test_an_open_batch_dispatches_repair():
    manifest, graph = _complete()
    batch = Batch(slice="listings", wave=0, issues=(), rerun=())
    got = select(manifest, graph, batches=(batch,))
    assert got is not None
    assert got.session_type == REPAIR
    assert got.scope["slice"] == "listings"


def test_a_pending_request_dispatches_triage():
    manifest, graph = _complete()
    got = select(manifest, graph, pending=("msg-0002",))
    assert got is not None
    assert got.session_type == TRIAGE
    assert got.scope["request"] == "msg-0002"


def test_behaviour_outside_every_slice_dispatches_slicing():
    """B*02 belongs to no slice. Architecture cannot be derived for it
    because there is no slice to own the result."""
    graph = Graph([_e("I·01"), _e("B·01", "I·01"), _e("B·02", "I·01")])
    manifest = _manifest(listings=("B·01",))
    got = select(manifest, graph)
    assert got is not None
    assert got.session_type == SLICING
    assert "B·02" in got.scope["unsliced"]


def test_an_unserved_entry_dispatches_derivation_for_the_layer_below():
    """B*01 has no child. The session writes architecture, not behaviour."""
    graph = Graph([_e("I·01"), _e("B·01", "I·01")])
    manifest = _manifest(listings=("B·01",))
    got = select(manifest, graph)
    assert got is not None
    assert got.session_type == DERIVATION
    assert got.scope["layer"] == "A"
    assert got.scope["slice"] == "listings"
    assert got.scope["parents"] == ["B·01"]


def test_unserved_intent_derives_behaviour_and_carries_no_slice():
    """Intent is not sliced -- slicing has not run yet at I -> B."""
    graph = Graph([_e("I·01")])
    got = select(_manifest(), graph)
    assert got is not None
    assert got.session_type == DERIVATION
    assert got.scope["layer"] == "B"
    assert got.scope["slice"] is None


def test_spec_with_no_module_dispatches_build():
    manifest, graph = _complete()
    bare = _manifest(listings=("B·01 A·01 S·01",))  # no modules
    got = select(bare, graph)
    assert got is not None
    assert got.session_type == BUILD
    assert got.scope["slice"] == "listings"


# -- the order ---------------------------------------------------------------


def test_back_check_outranks_everything():
    """In flight beats new: a correction that has not been validated upward
    must not have work built on top of it."""
    manifest, graph = _complete()
    batch = Batch(slice="listings", wave=0, issues=(), rerun=())
    got = select(
        manifest, graph, unchecked=("msg-1",), batches=(batch,), pending=("msg-2",)
    )
    assert got is not None and got.session_type == BACK_CHECK


def test_repair_outranks_triage():
    """A wave's fallout is finished before new input is let in, so it never
    trails into the next wave."""
    manifest, graph = _complete()
    batch = Batch(slice="listings", wave=0, issues=(), rerun=())
    got = select(manifest, graph, batches=(batch,), pending=("msg-2",))
    assert got is not None and got.session_type == REPAIR


def test_triage_outranks_slicing():
    """A correction reaches the graph before a partition is proposed on top
    of behaviour that is about to change."""
    graph = Graph([_e("I·01"), _e("B·01", "I·01")])
    got = select(_manifest(), graph, pending=("msg-2",))
    assert got is not None and got.session_type == TRIAGE


def test_slicing_outranks_derivation():
    """Deriving architecture for a slice while other behaviour is still
    unsliced would cut the partition around work already done."""
    graph = Graph([_e("I·01"), _e("B·01", "I·01"), _e("B·02", "I·01")])
    manifest = _manifest(listings=("B·01",))
    got = select(manifest, graph)
    assert got is not None and got.session_type == SLICING


def test_derivation_outranks_build():
    """A*01 is unserved, so the spec layer is incomplete -- building now
    would implement a slice whose spec is still being written."""
    graph = Graph(
        [
            _e("I·01"),
            _e("B·01", "I·01"),
            _e("A·01", "B·01"),
            _e("A·02", "B·01"),
            _e("S·01", "A·01"),
        ]
    )
    manifest = _manifest(listings=("B·01 A·01 A·02 S·01",))
    got = select(manifest, graph)
    assert got is not None and got.session_type == DERIVATION


def test_derivation_takes_the_earliest_wave_first():
    """`base` is depended on by `top`. Deriving `top` first would read a
    dependency that is still being written."""
    graph = Graph(
        [
            _e("I·01"),
            _e("B·01", "I·01"),
            _e("B·02", "I·01"),
            _e("A·01", "B·01", depends_on="A·02"),
            _e("A·02", "B·02"),
        ]
    )
    manifest = _manifest(top=("B·01 A·01",), base=("B·02 A·02",))
    got = select(manifest, graph)
    assert got is not None
    assert got.session_type == DERIVATION
    assert got.scope["slice"] == "base"


def test_every_dispatch_states_why_it_was_selected():
    """The reason is logged, so a run that picked the wrong thing can be
    read back without re-deriving the ladder."""
    manifest, graph = _complete()
    got = select(manifest, graph, pending=("msg-2",))
    assert got is not None and got.reason
    assert got.project == "m"


def test_build_takes_the_earliest_wave_first():
    """`top` depends on `base`. Building `top` first would implement against
    a dependency that has no code yet."""
    graph = Graph(
        [
            _e("I·01"),
            _e("B·01", "I·01"),
            _e("B·02", "I·01"),
            _e("A·01", "B·01"),
            _e("A·02", "B·02"),
            _e("S·01", "A·01", depends_on="S·02"),
            _e("S·02", "A·02"),
        ]
    )
    manifest = _manifest(top=("B·01 A·01 S·01",), base=("B·02 A·02 S·02",))
    got = select(manifest, graph)
    assert got is not None
    assert got.session_type == BUILD
    assert got.scope["slice"] == "base"
