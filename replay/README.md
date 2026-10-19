# replay — score gates against a project's real history

Test tooling, not a caller of the live unit. `harness.py` rebuilds a project's
graph and manifest as they stood after every graph-changing event in an
exported snapshot, runs a registry of gate functions over each state, and
scores each gate against an oracle of hand-mapped faults. It builds every
graph in memory and calls mu-spec's library functions directly
(`service._gate_report`, `waves.schedule`, ...). It opens no store, speaks no
HTTP, and writes nothing but the `--out` report.

```
python -m replay.harness <snapshot_dir> <oracle.json> [--out report.json]
python -m replay.harness C:\agents\teams\forge\analysis\dark-retro\snapshot\node replay\oracles\dark.json
```

The run starts with a **self-check**: the final replayed state must give the
node's own `gates.json` and `waves.json`, and the live spec + test set at the
`units.json` cut must equal that cut's unit entries. If it does not, the
reconstruction is wrong and the CLI exits 2 without scoring anything. Exit 2 also
means an oracle case names an id that is missing, unborn or retired at its
`earliest_step_visible`.

## How the history is rebuilt

| The export lacks | Recovered from |
|---|---|
| when an entry existed | born at the `derivation` event that lists it; retired when its successor (`superseded_by`) is born |
| edges as written | a live read carries edges across interface-preserving supersessions; each target is walked back to the version alive at the child's birth |
| slice membership over time | the derivation's `slice`, then `slicing` moves; a behaviour entry takes its architecture children's majority slice |
| slice type over time | a `classification` changes a type, so before its first one the slice had the other type |
| module ownership | no history exists; read from each live spec body's `Module:` line |

A step is one graph-changing event (`derivation`, `slicing`, `classification`).
A `correction` gets no step of its own: it is logged a millisecond after the
derivation that holds its successors, and the state is the same. Steps are
named by the event `seq` they follow; a cut's state is the last step before it.

## Plugging in a gate ticket

A gate is a function of the state at a step and the step before it:

```python
def gate(graph, manifest, prev_graph, prev_manifest) -> Iterable[str]:
    ...  # the entry ids it flags ("S-35"), or "slice:<name>" for a slice
```

`prev_*` is there for checks that need a before and after -- a same-wave edge
(M5) needs the waves the amendment landed in, a classification check (M8) needs
the ratification moment. `manifest.modules` holds the body-derived ownership.

1. Write the gate where it belongs in `mu_spec/` (it is the ticket's real
   deliverable), test-first as usual.
2. Add one line to `REGISTRY` in `harness.py`, named after the ticket:
   `"M2": import_ownership,` -- adapting the signature in a lambda if needed.
3. Run the CLI. The gate's row shows `caught/total` for every ticket's cases;
   its own column is the score. `false+` counts distinct ids it flagged that no
   case expects -- read them in the `--out` report, they are either noise or
   faults the oracle is missing.
4. A missed case is either a gate gap or an oracle case the gate cannot see by
   design; say which in the ticket.

Scoring: a case is caught at the first step at or after its
`earliest_step_visible` where the gate flags **every** expected id at once.

## The oracle

`oracles/dark.json` holds 32 cases from the DARK retrospective
(`findings.jsonl`, with `verify_note` preferred over `happened`). Each is
`{finding_id, pattern_id, gate_ticket, expected_ids, earliest_step_visible, note}`.
`expected_ids` are what the ideal gate for the ticket should flag; for M2 that
is the anchor spec entry missing the edge, even when the import sits in one of
its test scenarios. Coverage: M2 10, M3 6, M4 2, M5 2, M6 2, M7 6, M8 1, M9 2,
M10 1.

## Reading the baseline

Today's registry is today's gates: `sound` (every blocking kind and every slice
finding) and the three non-blocking kinds on their own rows. `sound` catches
nothing and flags no cut state -- DARK was sound at every cut, which is the
point. `untested` and `unserved` "catch" a few cases only because they fire on
every new entry until its children are derived, and a case is visible from the
step its entry was born: that is timing, not detection.

Known limits: module ownership is the bodies' claim, never the declared module
map, so "no module" (M10) means "no `Module:` line"; a fault visible only in
code, a ticket or a CI log is outside what any graph replay can score.
