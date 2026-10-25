# Tickets after they exist: reissue, withdrawal, waves, and the loop back

Ratified with Andrey on 2026-10-06. The consumer side was settled with the session
that owns it (Paperclip: ingest, path-guard, Case, Lain). Each rule below says which
side it binds. mu-spec never names that consumer in code. Everything consumer-specific
in this unit is a value in a project's configuration.

`docs/DESIGN.md` §4a covers what a work unit is. This document covers what happens to
its ticket after it has been created.

---

## 1. Identity: one live issue per (repo, unit key)

A unit key (`S-31`, `S-31-T`) has **at most one live issue** on a repository, across
every cut. A cut is a version of the content, never a new set of tickets.

Before this rule, the duplicate guard was scoped to `(cut_seq, repo)`. So every new cut
looked never-emitted, and pressing Ship created every issue a second time. That is how
DARK reached 288 issues for 144 units.

*Live* means this unit created the issue, and has not since recorded closing it.

**A unit key is its lineage root (M13, D1, ratified 2026-10-24).** The key is the first id
of the anchor's supersession chain, forever: `S-107` → `S-127` → `S-140` stays `S-107` /
`S-107-T`. The graph still mints new ids; only the name of the unit stops moving. The ticket
names the live entry: the title leads with it, `S-140 (rev of S-107) — <paths>` (leading,
because a long write set truncates the end of a title), the body opens with the chain, and
the machine block's `anchor` is the live id while `key` stays the root.

**One tree of issues per unit.** The root issue is created for the first id. Untouched and
open, it is edited in place as the anchor moves. Once closed, a contract change opens a
follow-up (§2) that is also attached as a GitHub **sub-issue under the root** -- every
follow-up, however many, under the same root. A link that fails is recorded and retried by
the next press; the follow-up is never created twice. A 422 for a link GitHub already holds
is done. Closed issues are never reopened by this unit; a parent found open after an attach
is reported in `raced`, and nothing is closed for it.

**Projects keyed the old way stay so.** A project whose emission record holds any line
without `"keys": "lineage"` -- DARK, frozen -- keeps its units keyed by their live anchor,
decided from that record and never configured. Its tickets render byte for byte as before,
`blocked_by_was` included. Its anchors no longer hand their issue to a renamed key: every
rename DARK made is already recorded under the new key, and DARK takes no new amendments.

## 2. Touched

An issue is **touched** when somebody other than this unit has acted on it. Once
touched, mu-spec never edits it and never closes it. It reports it instead.

    touched = closed for any reason this unit did not record
            OR carries any of the project's `pickup_labels`

- Today `pickup_labels` is `["cto:admitted"]`. The consumer's ingest adds that label
  **at admission**, before any work starts. Admission is the boundary, not the PR,
  because an edit after admission makes the consumer admit the unit twice.
- The whole check takes one GitHub read: the issue's state, its `state_reason`, and its
  labels.
- **A closed touched issue is `settled`, not reported -- unless its contract changed.**
  Ship fingerprints the `## Contract` section separately (identifiers masked, so a
  renumbering is not a change). If only the context moved -- blockers, renamed keys,
  labels, the other contracts a file serves -- the issue is done with: never edited,
  never reopened, no warning, only a count in the result. If the **contract** moved
  after the work merged, Ship opens a **follow-up**: a new open issue under the same
  key, titled `… (follow-up to #N)`, saying to build the difference or close it as
  already satisfied. Above its contract it carries `## Changed since #N`: each entry
  added, removed, renumbered (from each entry's `was`, the ids it superseded) or changed,
  with the changed text as a diff -- diffs dropped, ids kept, near the size ceiling. The key now lives on the follow-up, every order touching it is
  wired again to the new number, and dependents' `blocked_by_was` stops naming the old
  key, whose merged work is not the follow-up's. Records written before contracts were
  fingerprinted are compared against the closed issue's own Contract section, read in
  the same call as its state.
- **The write set is part of the contract (M26, D1).** The fingerprint (V4) covers the
  `## Files you may write` paths, sorted. A unit closed `completed` that now owns a file
  it did not own when it merged gets a follow-up -- under its root, as a sub-issue, on a
  lineage project -- never a reopen. Every older record (DARK's: V1, V2 or none) still
  compares the contract alone, so no closed issue moves because the fingerprint grew.
- **A renumber is no change (M25).** The fingerprint (V5) reads the contract's entries as
  a set: a unit lists its scenarios in id order, so a renumbered scenario moved to the end
  and V4 saw a change in an unchanged contract. A V4 record is compared through the closed
  issue's own text, and every older one is also settled when its entries are the same set.
  Measured on DARK: its 91 recorded follow-ups and the three named as relabels (#503, #810,
  #829) each also changed text, so none would have been spared -- restatements merged code
  already met need code read, which this unit does not do.
- **The code follows its tests (T-623).** A press that opens a follow-up for a test unit
  while its implementation is closed `completed` opens one for the implementation too,
  under its root, headed `## Tests changed since #N`: make the code pass them, or close
  it as already satisfied. Signal: the test unit's contract moved in this press. A press
  that dies between the two loses the second (not persisted). mu-spec cannot tell whether merged code already satisfies
  a changed contract -- that needs reading code -- so the follow-up says to close it
  if so. The report (`touched`) is kept for **open** issues somebody holds,
  the one case where "release it and ship again" is advice anyone can act on.
- **With no `pickup_labels` configured, nothing is ever edited.** A changed, open issue
  is reported as changed but left alone. Without a pickup signal, "untouched" can't be
  told apart from "admitted", so the safe reading is that every open issue is admitted.

## 3. Reissue is a sync

**The tracker is read once per press, and what it says outranks a lost record.** Before
deciding anything, Ship lists the repository's open issues and keeps this project's
(a `mu-spec:` label and a machine block naming the project). A key with no recorded
issue but an open one of ours **adopts** it -- the oldest -- instead of creating a
second; an open follow-up is adopted the same way. At the end of the press, an open
issue of ours that **no key owns** and nobody picked up is **swept**: closed
`not_planned`. A picked-up orphan is left alone and reported. The record is written
even when a press dies part-way, and a write that timed out is reported, never
repeated, because it may have landed.

Why: on DARK, cut 8, a read timeout killed two presses after their issues were
created and before their record was written; the retry created the same thirty
again, and a follow-up comparison against an empty body opened forty-six more.
Adopt-then-sweep is what lets the next press repair that without anyone closing
issues by hand.

Pressing Ship compares the current cut with what is live, and acts per unit:

| | untouched | touched |
|---|---|---|
| content unchanged | nothing | nothing |
| content changed | **edit in place**: title, body, our labels, `blocked_by` | report if open; settled if closed |
| unit is new | **create** | — |
| unit gone from the cut | **close `not_planned`** | report if open; settled if closed |

- **"Changed" is measured on what the ticket says.** The hash covers the title, the
  body and our labels, rendered *without* `cut_seq` and `batch`. A new cut whose content
  for a unit is identical doesn't touch that issue. It keeps its old `cut_seq` and
  `batch`. The consumer deduplicates on the key, so a stale `cut_seq` is harmless.
- **Labels on edit.** Ours are replaced: `mu-spec:*`, `kind:*`, `slice:*` and the
  project's configured labels. Any other label on the issue is kept.
- **`blocked_by` on edit.** It is made to match the cut. A dependency the cut no longer
  has is removed (`DELETE …/dependencies/blocked_by/{id}`). Leaving one behind would be
  a real fault, not just clutter. The consumer treats a blocker closed `not_planned` as
  never satisfied, so a stale edge to a withdrawn unit blocks its dependent forever.
- **`blocked_by` as GitHub holds it.** The record keeps pairs of keys, not which issue a
  link points at, so a key that moved issue left links the pairs called wired (DARK:
  24 links to swept follow-ups). Each press reads the native `blocked_by` of every open
  issue nobody picked up, adds what is missing, and removes links to *our* issues
  (`mu-spec:` label) the cut does not want. Links to anyone else's issues are kept;
  a picked-up issue's links are never touched.
- **Checked after the press, as a consumer reads it.** Every blocker an open, unpicked
  issue of ours names must be carried -- under that key or one `blocked_by_was` lists
  for it -- by an issue that is open or closed `completed`. Anything else is reported
  in `dead` and the press shows as failed (DARK: ~30 units behind keys whose only issue
  was closed `not_planned`; S-273 behind a key no issue carried). A closed blocker
  costs one read; an open one costs none.
- **Read before writing, and read again after.** Each edit or close re-reads the issue
  immediately before writing. Afterwards it reads once more. If a pickup label appeared
  in that gap, the result reports the issue as **edited during admission**. The
  consumer's side of the race is in §8. Together they leave no silent window.
- **Unchanged units cost no reads at all.** A sync of an unmoved cut makes zero calls.

## 4. Withdrawal

Withdrawal is the sync run against an empty cut. It closes every live, untouched
issue on the repository, from any cut, as `not_planned`, highest number first. Touched
issues are reported and left. A `completed` close is never overwritten (that was the
#145 defect). An issue that GitHub refuses to close stays live, so it is never
recreated.

GitHub's REST API can't delete issues, and nothing here asks to. Closing is the only
way an issue is ever revoked.

## 5. Waves: the horizon

Ship takes a **horizon N**: the number of build-order waves to create issues for in this
press. N is not concurrency. How many agents work in parallel is entirely the
consumer's business.

- The press creates issues for units in the **first N waves that still contain a
  unit with no live issue**. Units outside the horizon get no issue yet.
- **Or the horizon is `"shipped"`**: create only the units that work already on the
  tracker waits on, transitively -- the gaps under the frontier, never past it. Wave
  numbers are a view and a re-cut renumbers them (DARK's waves 1–3 spread across 15
  after a slice reclassification), so the rule follows edges, not wave indices. It is
  the press to make after a spec change, and nobody has to remember how far the last
  press reached. An implementation whose own test unit is out is included too, with
  what it waits on (never what waits on it): merged tests with no code are broken
  whether or not anything follows it -- DARK's `tests/test_health.py` could not even
  be collected. **Two more count (M29):** a unit owning a module path, dotted module or
  `Exercises:` id that the scenarios of a test unit already out name -- read off the
  bodies, never the repository (DARK S-229, S-134: merged tests imported them, main stayed
  red) -- with what it waits on; and a **new leaf one step past the frontier**: nothing
  waits on it, and everything it and its own test unit wait on is out or comes in this
  press (DARK S-331, the study, held by two presses). Any other new unit is new work and
  waits for a numeric press. The dashboard offers it by default once anything is out and
  shows the unit's own preview: how many it creates and how many it **holds**. With nothing
  out it creates nothing.
- Edits and closes always apply to *every* live issue, whatever the horizon. The horizon
  limits creation only.
- **Why it exists:** later waves are derived before the code of earlier waves exists.
  What wave 1 teaches corrects the spec for wave 4, so shipping wave 4 early guarantees
  rework.
- **Default N is 1** until the loop has been seen working once. On DARK's cut 3 the
  wave widths are 22, 36, 27, 20, 12, 9, 6, 3, 2, 3, 3, 1.
- **Ordering across presses needs nothing new.** A blocker always sits in an earlier
  wave, or is created in the same press. The consumer admits a unit only when every
  `blocked_by` key is closed `completed`. A blocker not yet shipped just waits.
- **A blocker may be closed under an old key** (projects keyed by live anchor only; a
  lineage key never changes, so the map below is always `{}`). The machine block also carries
  `blocked_by_was`: blocker key → the keys it had before its anchor was superseded
  (§1), e.g. `{"S-127-T": ["S-107-T"]}`, and `{}` when no blocker was renamed. A
  touched issue is never edited, so its own block can still say `S-107-T`. A blocker
  counts as closed `completed` if a closed `completed` issue's block carries its key **or
  any key listed for it here**.

**Merge order (M31, D11, ratified 2026-10-23).** Some orders keep main green without
being dependencies, and the edge rules cannot state them (DARK m1210-3, m1206-17: forge
held S-325 and #645 by hand). Ship derives them on every press from its own record --
**never stored as an entry edge** -- for one shape: an open **test** follow-up of unit D
and an open **implementation** follow-up of a unit D waits on, transitively. The one
opened later merges first, because it is the repair: S-325's follow-up waited for the
tests restated after it to pass on both sides of it (m1210-3); S-271-T's test follow-up
waited for the S-217 storage fix filed after it (m1206-17).
- A derived blocker is in the machine block's `blocked_by`, so a consumer waits on it
  like any other, and in `"order": {"<key>": "merge"}`, so it can tell it from a
  dependency. The field is absent when there is none. GitHub gets the native link.
- Derived again after this press's follow-ups exist, and the unit it orders is edited in
  place in the same press. It leaves when its blocker closes.
- **A loop never refuses (ratified 2026-10-24).** Projected edges and merge orders
  together are walked; the merge orders inside a loop are **dropped and reported** in the
  result's `dropped_orders` (`{"loop": [A, B, C, A], "orders": [{blocked, blocker}]}`, and
  a count), and every other order stands. The press goes on. Only a loop through the
  units' own edges refuses, a 400 naming it, before anything is written -- the gates make
  that impossible, so it is a guard.

## 5a. One unit, and what a press checks first (M30)

DARK shipped two 144-issue batches and withdrew both before one ticket was seen to ingest
(p09-1, m1201-8). So:

- **One unit.** `POST /emit {"unit": "S-31"}` -- a person's press like any other, from the
  dashboard's third horizon choice. That unit is created or edited, the orders touching it
  are wired, and nothing else is written: no other edit, no withdrawal, no sweep.
- **The build.** The pane shows the build answering `GET /emit` (`build`) and sends it with
  the press; a process that is another build answers 409 before any job (DARK p03-2:
  a cut shipped from a node behind the emitter; cross-10: three builds live at once).
- **The labels.** Before any write, the repository is asked whether the token has push
  access (`GET /repos/{repo}`); without it GitHub creates every issue and drops its labels
  silently, so the press refuses. After each create, the labels GitHub returned are
  compared with those sent and a loss is reported in `unlabelled` (DARK m1109-5).

## 6. Batch

Every ticket's machine block carries `batch`: the sequence number of the Ship press that
created or last edited it. A batch is never renumbered.

A wave number would not work here, because it is recomputed every cut. If a unit is
inserted ahead of S-31, S-31 moves from wave 1 to wave 2. But once admitted, S-31's
issue is frozen and still says wave 1, so the two sides would disagree about which wave
is finished.

## 7. What a ticket carries

Every ticket carries, in full:
- its own contract
- the entry immediately above it

The intent and behaviour ancestors appear as titles only.

**Option A, ratified 2026-10-06:** the contracts of *other units writing the same file*
(`## Other contracts these files must also serve`) are inlined in full whenever the
ticket still fits GitHub's 65,536-character limit.
- That was the gap on the one real run. S-01-T knew `Provisional` (defined by S-02, the
  same file) only by its title, so it read nine other issues to find it. Under a
  horizon, those issues often won't exist yet.
- Measured on DARK with the real renderer: the median ticket grows from 17 KB to 24 KB, and
  139 of 144 carry their neighbours in full.
- The ticket isn't the cost driver. For one run (S-01-T, $4.00 without the cancelled
  run), the ticket was about 8% of cached input. Turns multiplied by context dominated
  the cost, and every lookup Case is spared removes turns.

**The 5 that don't fit** (S-105, S-107, S-38-T, S-69, S-76-T) keep titles, and list the ids in
the machine block as `context_ids`. Fetching those is a consumer decision. The
candidate is Paperclip's `plan` document, which is injected into the prompt with no cap.
It isn't built.

Contracts the unit *depends on* stay as titles. In DARK, every one of them is built by
a unit it waits on, so the code is in the repository by the time the ticket is worked.

**M27, ratified 2026-10-23 (D3):**
- **A shared file that exists goes as titles.** Once a unit writing the file is closed
  `completed` in mu-spec's own emission record (a settled marker; no repository read,
  no extra GitHub call), its other contracts are listed as id + title. The file's shape
  is decided and is the code. Before that they are inlined as above. The unit's own
  contract is never shortened.
- **No provenance.** The fixed-shape lines `(Supersedes S-NN…)` and `(Raised as
  iss-NNNN…)` (a whole line, optionally in `*` or `_`) are stripped from every rendered
  body. Nothing else is touched: a ruling paragraph can carry contract text. The contract
  fingerprint reads the contract without them, so stripping opens no follow-up.
- **Never a superseded id (ruling (b), 2026-10-24).** As a ticket renders, every
  retired id cited in entry text whose chain ends in a live entry is written
  `S-317 (was S-128)`. Stored entries are untouched; unit keys (title, blockers, overlap,
  the machine block) are names, not citations, and are never rewritten. The contract
  fingerprint reads `X (was Y)` as `X`, so a rewrite alone opens no follow-up. Before a
  press writes anything every ticket is rendered, and only a cited id whose chain ends
  in no live entry refuses the press: a 400 naming the unit, the id and the entry
  citing it.
- Reported, never refused (completeness findings): an S-NN in an entry's body that none
  of its edges reach (`unlinked_mention`), and a live entry whose body still cites a
  retired id, with its successor (`retired_mention`), so the prose can be cleaned.

The machine block gains `project`, `batch` and `context_ids`. All are additive, and the
consumer's parser ignores unknown fields.

## 8. The consumer's half (binding on their side, recorded here so both sides read one list)

1. **Add `cto:admitted` at admission, then re-read the body, then admit from the
   re-read.** That ordering closes the race from their side.
2. **Deduplicate admissions on `repo#key`, never `@cut_seq`.** One-time migration:
   `DARK#S-01-T@3` → `DARK#S-01-T`.
3. **Case refuses to start on an issue without `cto:admitted`.** That makes the label
   the only way work starts. It also closes the side doors: a board assignment, TARS as
   superuser.
4. **`completed` wins** when two issues share a key, regardless of the order they were
   read in.
5. **Review trigger.** When every issue in batch B is closed (`completed` or
   `not_planned`), the ingest files one `[wave-review:<repo>#B]` task for Lain.
   - The review is **strict**: no partial review.
   - Stall alert: if a batch has open issues and no close for **48 h**, a board-visible
     `[batch-stalled]` task is filed. It never triggers a review on its own.
   - Andrey revisits both after the first live loop.
6. **Lain is the only agent on the mu-spec and holonic MCPs.** Case and every other
   agent have no access.

## 9. The loop back: findings and stops

Findings go to mu-spec automatically. A finding is filed as a mu-spec issue
(`raise_issue`), never as a GitHub comment.

**`raise_issue` fields:**
- **target:** the **entry** whose text is wrong, e.g. `S-31` or `A-07`. Never a unit
  key: `S-31-T` → `S-31`.
- **kind:** `additive` (a new entry is needed) or `semantic` (an existing entry means
  something else).
- **claim:** one line.
- **assumption:** what had to be assumed in order to proceed.
- **raised_by:** who filed it.

One finding per entry.

**Two levels:**
- **At wave review:** Lain files each finding and the team continues. Nothing new
  arrives until Andrey amends the spec anyway.
- **Inside one unit:** Case stops when the contract is **contradictory or
  insufficient**. ("Insufficient" is a stop reason added 2026-10-06.) Then:
  1. Case sets its task `blocked` and reassigns it to Lain, with three things: the
     unit, the contradiction or gap, and what it would have had to assume.
  2. Lain sorts it:
     - a spec problem becomes `raise_issue` with Case's assumption as the
       `assumption`, and the unit stays blocked;
     - an environment or tooling problem stays a task on the consumer side.

**Release after a spec fix (manual, by Andrey, for now):**
1. Andrey amends the spec and takes a new cut.
2. Andrey releases the unit, dispatching Lain: cancel the consumer's mirror, clear the
   failed branch and worktree, and **remove `cto:admitted`**. The unit is now
   untouched.
3. Ship edits the issue in place, and the ingest admits it again.

If Ship runs before the release, it reports the unit as changed but touched. That is
harmless: release it, then ship again. Automatic reopening is a later design.

## 10. What mu-spec still does not do

It never:
- reads an issue to decide what work to do
- comments on an issue
- reacts to a PR
- reopens anything
- decides that a unit is stale

Every write it makes was triggered by a person pressing Ship or Withdraw. Every read
exists only to avoid destroying somebody else's state while making those writes.

**One delegated exception (M33, D2, ratified 2026-10-24).** A person may grant a
project a standing **delegation** (`POST /projects/{p}/delegation` `{enabled, note}`,
a dashboard press, never a tool; recorded as a `delegation` event and in the change
log, with the actor). While it stands, an agent may call `auto_ship` and cut and Ship
on its own judgement -- only when ALL of these hold, each computed, none judged:

1. **gates** -- the graph is sound, modules are declared, and nothing Ship would
   refuse: no dead-end citation, no loop in the units' own edges, and no shipped,
   unsettled unit waiting on a blocker with no live issue (or one closed
   `not_planned`) in the emission record;
2. **st_only** -- since the cut the latest press shipped, no I, B or A entry was
   added or superseded: no entry of those layers is numbered above the per-layer
   marks that cut recorded (`spec_diff` per layer). A cut written before marks were
   recorded cannot be compared and refuses until a person cuts and ships once;
3. **shipped** -- every unit the change touches already has an issue: every key in
   a drift group between that cut and the live projection, and every key whose
   edges moved.

Also `delegation` (granted, not revoked) and `ready` (repo, token, no press in
flight). Any failure is a 409 naming the conditions, and nothing is written. When
all hold it runs the person's `cut_units`, records an `auto_ship` event (actor,
cut, base cut, every condition as evaluated), then the person's Ship press as a job
-- with a horizon of zero: it edits, follows up and withdraws, and never opens an
issue for a unit that has none. Its completion is the same `tickets_emitted` event.
Every finished Ship run -- person's press, one-unit press or delegated -- writes
exactly one lifecycle event with the run's `run_id`: `tickets_emitted` when it
emitted, `ship_failed` (`error` or `reason`, `cut_seq`, counts reached) when it
raised or refused (10-25). The in-memory run status dies with the process; these do not.
Anything touching I, B or A keeps the person's press.
