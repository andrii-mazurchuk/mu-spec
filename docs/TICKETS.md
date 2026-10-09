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

**A superseded anchor keeps its issue.** A unit key is its spec anchor, so superseding
`S-107` with `S-127` renames units `S-107`/`S-107-T` to `S-127`/`S-127-T`. The work is the
same, so the new key takes over the old key's issue and edges: it is edited in place if
untouched and reported if touched (§2). It is never withdrawn and recreated. The issue's
title and machine block then carry the new key, and a consumer matching on `repo#key` sees a
new key on an issue number it already knows.

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
- **With no `pickup_labels` configured, nothing is ever edited.** A changed, open issue
  is reported as changed but left alone. Without a pickup signal, "untouched" can't be
  told apart from "admitted", so the safe reading is that every open issue is admitted.

## 3. Reissue is a sync

Pressing Ship compares the current cut with what is live, and acts per unit:

| | untouched | touched |
|---|---|---|
| content unchanged | nothing | nothing |
| content changed | **edit in place**: title, body, our labels, `blocked_by` | report |
| unit is new | **create** | — |
| unit gone from the cut | **close `not_planned`** | report |

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
- **A blocker may be closed under an old key.** The machine block also carries
  `blocked_by_was`: blocker key → the keys it had before its anchor was superseded
  (§1), e.g. `{"S-127-T": ["S-107-T"]}`, and `{}` when no blocker was renamed. A
  touched issue is never edited, so its own block can still say `S-107-T`. A blocker
  counts as closed `completed` if a closed `completed` issue's block carries its key **or
  any key listed for it here**.

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
