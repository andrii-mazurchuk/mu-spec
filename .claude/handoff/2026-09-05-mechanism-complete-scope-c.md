# Handoff — 2026-09-05 — mu-spec: the mechanism is complete, scope C is built, no agent exists

> Previous handoff: `2026-09-04-mu-spec-pipeline-design.md` — pipeline built, agent design
> settled, cross-cutting contested.

## Session summary

Last session ended with cross-cutting unresolved and a list of unbuilt mechanisms. This
session Andrey dropped `architecture-decisions.md` into the handoff folder — a
consolidated design worked out with another agent — and asked for a diff against ours
before merging. That diff was produced, ruled on, and then implemented in full.

**Every mechanical operation the architecture calls for is now built and tested: 328
tests, walkthrough green, twelve commits.** No agent session exists — nothing derives
anything, and `walkthrough.py` still hand-writes every entry. That gap is the whole
remaining project.

Scope C (measurement) was designed in conversation, corrected by Andrey on the
metrics-vs-logs distinction, and built.

## What's done

Twelve commits from `68db89e` to `a09193d`. In order:

- **Adjacent-layer `derives_from`** — skipping a layer is refused. Exposed a real hole:
  the `unserved` gate counted any declared child, so a bogus skip-edge silenced it.
- **JSON Lines storage** — markdown entry files gone; net deletion of parser code.
- **Entry-level `depends_on`, slice dependency projected** — the biggest change.
  `Slice.depends_on` is gone; there is deliberately no field to author it in.
- **Cross-cutting as a slice type** — the contested question, closed (see below).
- **Two slice gates** — cycles, and a concern reaching into a feature slice. Writing the
  second test found a back door: build edges first, relabel after. `classify_slice` now
  checks before it writes.
- **`emits_into`** — the third edge kind. Resolves the case-4 shape.
- **Waves** — longest path. Cross-cutting lands at wave 0 *by construction*.
- **The issue queue and header-only router** — plus entry-level re-run scope, the
  two-round cap, and backward-reach escalation.
- **Spec to code** — module backlinks, spec diff (no history file needed), write/read
  set, the git-diff audit.
- **One entry, one slice** — was a documented invariant that nothing enforced.
- **Scope C** — lifecycle log, metrics, proposal scoring, shipping.
- **Docs** — all persistent docs current.

## Decisions made this session

**Cross-cutting is a slice type, decided by two tests** — does the caller branch on what
comes back, and does the contract name a domain object someone else owns. Both must pass.
Fan-in is never the criterion; a slice everything depends on is *foundational*, not
cross-cutting.

The three objections that blocked this last session dissolved without needing three new
rules: the classification test and the edge rules turned out to be the same fact seen
twice. And the `auth ↔ notifications` loop that made the objection concrete was a
category error on my part — under the two tests *neither is cross-cutting*.

**Slice dependency is projected from entry edges, never authored.** Two statements of the
same fact drift.

**All conflicts hard-block** (Andrey's ruling) — but only things that are definitionally
broken. Scope C metrics never gate.

**Andrey ruled option A on logging**: a new `entry_type` in mu-logs is fine. mu-spec ships
`project_event` today and degrades until the vocabulary is updated there.

**Metrics ≠ logs** (Andrey's correction). `/stats` carries already-processed aggregates,
no text. Logs carry the rich lifecycle. Everything agent-facing is a registered tool.

**No text analysis in this unit.** Grouping by shared nouns stays the reading agent's job.

## Current state

**328 tests. `python walkthrough.py` runs the whole pipeline and prints every stage** —
it is by far the fastest way to see what this does.

Nothing is pushed. `git log --oneline` shows twelve unpushed commits on `main`; the remote
`https://github.com/andrii-mazurchuk/mu_spec.git` may not exist yet.

## ▶ Next step

**Two things need Andrey, and neither blocks the other:**

1. **Add `project_event` to mu-logs' `entry_type` vocabulary.** It is a closed list
   enforced in that unit's code. Until then every ship is refused and mu-spec carries on.
   Worth pairing with a doc fix found on the way: mu-logs' README documents two entry
   types, the code accepts four (`cap_check` and `system_message` are real and
   undocumented, and the gateway uses `system_message`).
2. **Push.** Nothing has left this machine.

**Then the actual next project: build one agent session, end to end.** Not more
mechanism. The mechanical half is more finished than the design assumed and the agent half
has not moved at all. One session that derives behaviour from intent will reveal what the
design got wrong faster than any further building.

`DESIGN.md` §11 lists **session boundaries** as the open item most likely to bite —
everything else open is a field shape.

## Files & references to read

**Read these first:**

- **`docs/DESIGN.md`** — the architecture, now current. §4.5 cross-cutting, §6 gates, §6a
  waves, §9a issues, §10a measurement. §11 is what is still open.
- **`CLAUDE.md`** — self-contained, includes the system standard and a module map.
- **The artifact** — https://claude.ai/code/artifact/af605511-76e5-4bad-bd17-787b5ffb1069
  Updated this session; diagrams of the whole machine. Andrey reads visually. Update it
  in place by passing that URL as `url`.
- **`.claude/handoff/architecture-decisions.md`** — the consolidated design Andrey
  supplied. Authoritative on *design*; this repo is authoritative on storage mechanics.

**Then the code, in dependency order:**

`identifiers` → `graph` → `storage` → `gates` / `slice_gates` → `waves` → `issues` /
`reconcile` → `planning` → `lifecycle` / `metrics` / `slicing` → `service` → `server`.

Every module's docstring explains *why* it exists, not what it does. `service.py` is the
whole operation surface; `server.py` holds the hand-written tool manifest.

## Gotchas / warnings

- **Do not read other units' source.** `.claude/settings.json` denies `../**`. Spawn a
  subagent and ask it for the *contract*, never the code — done twice now, works well.
- **`entry_type` in mu-logs is closed and enforced in code.** Four values today.
- **No `/metrics` endpoint exists** anywhere in the system. The standard is `/stats` with
  a `metrics` field inside it. Confirmed by investigation, not assumed.
- **mu-logs has no retention, no size cap, no cardinality limit.** Rows are never deleted.
  An argument against shipping high-cardinality domain events there without thought.
- **`dcg` blocks file deletion and some heredoc patterns.** Write patch scripts to a file
  in the scratchpad and run them; `python3 - <<'PYEOF'` works, but heredocs containing
  apostrophes inside triple-quoted strings get mangled.
- **Never let a metric gate.** It is written into `CLAUDE.md` as an invariant. The failure
  mode is somebody deciding low cohesion should refuse an amendment.
- **Andrey wants to be consulted before implementation** on design questions. He was
  right to insist on it for cross-cutting — the objection produced a better answer.
- **Keep the vocabulary plain.** "approval step" not ratification, "cross-slice check" not
  reconciliation, "conflict" not contradiction.

## What is deliberately *not* built

- **Every agent session.** All five types.
- Session logging, cost limits, timeouts.
- The `decision` inbox type — approval in plain words.
- Coverage gaps: what the behaviour layer never mentions but obviously needs.
- A holding place for behaviour written before slicing runs.
