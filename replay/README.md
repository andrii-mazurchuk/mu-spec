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
node's own `gates.json` (with the kinds added after the export, `NEWER_KINDS`,
taken out) and `waves.json`, and the live spec + test set at the
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
| module ownership | no history exists; each spec body's `Module:` line, plus the final `modules.json` for entries live at the export (the only source for test modules), taken as declared at birth |

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

`M2` is `bad_import` + `bad_module_import` fed by `body.legacy_imports`:
DARK predates the D4 `Imports:` line, so the strict reader -- the only one
live admission uses -- has nothing to read. `M3` is `split_ownership` +
`undeclared_path`. `two_successors` is always empty here: the export holds one
`superseded_by` per entry, so a replay cannot express two claims.

The report-only rows (D3, none of them refuses): `M4` is `undefined_name` fed
by `body.legacy_imports`, as M2 is; `M5` is `same_wave_edge`, the only row that
reads the step before (the edge itself moves the slice, so the after state
alone never shows it); `M6` `unknown_member`, `M7` `sweep_collision` and `M10`
`no_module` are the gate report's own kinds. (`M9` was built and dropped
10-24: 0/2 cases, it never fired -- DARK's B/A bodies hold no code names.
`M22`, a test building a type declared outside its anchor and `Exercises:`
line, was built and dropped 10-24: 0/3 muspec-spec-14 cases, 37 tests flagged
at cut 45 (the peak), 1 in 5 sampled genuine -- the broken fixtures were in
test helper code, which no T body describes.)
Every one is a regex approximation; a missed case whose fault is only in the
prose's meaning (S-195's unnamed prediction type, S-251's missing velocity,
A-145's uncalled `tune()`) is one no mechanical row can see.

Known limits: module ownership before the export is the bodies' claim, so "no
module" (M10) means "no `Module:` line" for a retired entry -- and an entry live
at the export takes its final declarations from birth, so M10 cannot see that
S-73, S-77 and S-103 had no module at cut 3; a fault visible
only in code, a ticket or a CI log is outside what any graph replay can score.

## Comparing a project against DARK (V3, and how V4 uses it)

`baseline.py` measures a snapshot on the numbers two projects can share, and
`compare.py` prints them side by side. Both report; neither gates, and the exit
code never depends on a number.

```
python -m replay.baseline <snapshot_dir> [--ledger forge-metrics.json] [--out file.json]
python -m replay.compare replay\baselines\dark.json <current> [--ledger forge-metrics.json]
```

`<current>` is a file `replay.baseline` wrote, a snapshot directory, or a live
project URL (`http://127.0.0.1:9006/projects/<name>`, read-only through GET
`/insights` and `/spine`; supersessions are `n/a` there, history is not served).

Two kinds of number (`METRICS` and `LEDGER` in `baseline.py`, each with the
direction that counts as better, or none for size context):

- **Computed by mu-spec** from the replayed graph, requests and issues through
  `mu_spec.metrics`: live entries by layer, slices, wave depth, supersessions
  (and per live entry), change locality mean and single-slice share,
  corrections and the layer they entered at, weighted cohesion, issues
  (semantic, per live spec).
- **Supplied by forge's ledger**, passed through: stops, spec-fault rulings,
  rulings with no issue, permission denials, first-attempt share, follow-ups,
  idle gaps over 1 h, cost and median lead time -- per unit where a count
  scales. A retro-shaped `index/metrics.json` is normalised; a flat file in
  `LEDGER` names is taken as is.

`baselines/dark.json` is DARK's: the export's final state plus one line per cut,
with the snapshot, its last event and the code sha in `provenance`.
`disagrees_with_node_insights` compares the replay with the node's own
`insights.json` on every field both hold; empty means the replay is right.

**V4.** When the pilot is done, export its snapshot the way DARK's was taken,
have forge produce its ledger metrics, then
`python -m replay.compare replay\baselines\dark.json <pilot snapshot> --ledger <pilot ledger>`.
Mid-run, point `<current>` at the live project URL. Read each `worse` against
its size context: a smaller pilot is not a better one. Write the pilot's own
baseline next to DARK's with `replay.baseline ... --out replay\baselines\<pilot>.json`.
