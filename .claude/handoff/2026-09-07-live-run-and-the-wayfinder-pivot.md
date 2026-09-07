# Handoff — 2026-09-07 — the live run, and the pivot to wayfinder

> Previous handoff: `2026-09-05-mechanism-complete-scope-c.md` — mechanism complete,
> scope C built, no agent existed.

## Session summary

Last session ended with every mechanical operation built and no agent session in
existence. This session built the agent half, **ran it for real**, and then removed it
again — because running it is what proved the design was wrong.

Twenty-four live `claude -p` sessions drove a real project (a Telegram finance bot) from
one `initiate` request to **9 intent → 27 behaviour → 34 architecture → 8 spec**, sound.
It cost 2.9 hours, found **14 real bugs** that 378 green tests had missed, and stopped on
an account session limit. The output quality was high; the economics were not.

Andrey then redirected: keep the four layers, throw away the driver, and rebuild the
processing around the **wayfinder** skill with a human in the loop. The session ended
with the driver stripped and mu-spec back to being a pure tool unit that documents its
own use.

## What's done

**Built, run, and then deliberately removed:** dispatch ladder, session runner, pipeline
loop, `POST /trigger`, six session-type contracts. All gone in `9e97cb5`. The live run is
the reason they existed and the reason they don't.

**Kept — every bug the run found, because none were about sessions:**

- paths are percent-decoded, so `get_entry` works at all (it 404'd for *every* identifier
  ever issued, since all contain U+00B7)
- behaviour has a holding place before slicing exists — storage demanded a slice name for
  layer B while slices are cut *after* behaviour, so the designed order was impossible
- a refused amendment burns no identifiers (`allocate` ran before `append`; B·01–B·04 are
  permanently burned in the live project)
- **an amendment writes one layer, enforced** — a repair agent wrote a behaviour and the
  architecture deriving from it in one transaction, and every gate passed
- an issue with no owning slice escalates instead of manufacturing a batch against a
  slice named `""`
- an issue's owning slice is **projected from the manifest**, not copied onto the issue
  and left to rot

**The slicing proposal + ratification gate** (`6850f2a`) — a proposal with somewhere to
wait for a human. Built last, survives the pivot, and is the shape the next design is
built out of.

**Three prompt tiers** (`319c435`): `reference` rewritten from an intake interview into
the authoring contract; `wayfinding` added — one map per layer, what a decision must
become, which skill resolves which question; `default` points at both.

**`GET /skills`** — names `wayfinder`, `grilling`, `domain-modeling`, `research`,
`prototype` with `shipped_by_this_unit: false` on each.

19 commits, **346 tests**, 16 modules, 28 tools.

## Current state / open threads

**mu-spec is finished for now** and back to *computes, never reasons, never executes*.
Nothing is half-built in the repo.

**Live project data is still on disk** at `state/projects/personal-finance-bot` — 9
intent, 27 behaviour, 34 architecture, 8 spec, five ratified slices, 6 issues escalated
as `unowned`. It is gitignored. Keep it: it is the only real corpus for testing
retrieval, and `S·04`/`S·06` are worth reading as evidence the layers work.

**Nothing wayfinder-side exists yet.** No tracker doc, no Taskwarrior, no setup skill, no
processing-unit standard.

**A testing-report artifact was requested and never built.** Andrey asked for a new
artifact showcasing how the testing went and what it surfaced. Still owed.

## ▶ Next step

**Resolve the processing unit standard together with Taskwarrior.** In order:

1. **Write the Taskwarrior tracker doc** — the six wayfinding operations (map, child
   ticket, blocking, frontier, claim, resolve) as `## Wayfinding operations`. Two come
   free: `depends:` is native blocking and the `+READY` virtual tag *is* the frontier.
   Scope via hierarchical `project:<effort>.<layer>` plus contexts. `wf_type` as a UDA for
   research/prototype/grilling/task. **Taskwarrior is not installed** — `task` is not on
   PATH and there is no `~/.taskrc`.
2. **Settle the processing-unit standard.** The proposal on the table, not yet ratified:
   *a processing unit executes ratified work and never decides what the work should be.*
   PU moves to the **end** of the pipeline (executing GitHub issues), not the middle.
3. **The setup skill** — modelled on `setup-matt-pocock-skills`: explore, ask, then write
   `docs/agents/issue-tracker.md` and an `## Agent skills` block into the target repo's
   `CLAUDE.md`. Profile-based, software first.

## Files & references to read

- **`prompts/wayfinding.md`** — the layered-effort contract written this session. Read
  first; it is the design in prose.
- **`prompts/reference.md`** — what the unit accepts and refuses, and why.
- **`docs/DESIGN.md` §10b** — one writer, one layer, and the argument for it.
- **`CLAUDE.md`** — the boundary, restored. The module table is current.
- **`.claude/handoff/architecture-decisions.md`** — still authoritative on layer design.
- **`C:\Users\andre\AppData\Local\Temp\claude\...\scratchpad\mps`** — a clone of
  `mattpocock/skills`. May be gone; re-clone with `gh` if so.

**The three upstream skills that matter**, in `mattpocock/skills`:
`skills/engineering/wayfinder/SKILL.md`, plus `grilling`, `research`, `prototype`,
`domain-modeling`, and `setup-matt-pocock-skills` with its tracker templates.

## Gotchas / warnings

- **Wayfinder is user-invoked, so no skill may call it.** Their hard invariant: a
  user-invoked skill can never reach another user-invoked skill. The designed extension
  point is the map's `## Notes` block — name a model-invoked skill there and every session
  on that map consults it. Extend an *effort*, not the skill.
- **A tracker doc is the real graft point**, not the ticket types. Exactly six operations,
  documented per backend. Wayfinder's body never changes.
- **Taskwarrior must not become a memory unit.** Enclosure requires nothing else touches
  the storage, and the whole point is that Andrey runs `task` by hand. The contract would
  be a lie on day one. Andrey reached this conclusion himself; do not reopen it.
- **Skills do not ship from mu-spec.** There is no sharing standard in this system yet,
  and a unit that started shipping them would be setting that standard by accident. Cross-
  reference by name in prompts; `/skills` declares the dependency.
- **A trivial session costs ~$0.29 and ~27,500 cache-creation tokens** before reading a
  word. Our prompts were only 9% of that — trimming prose is not the lever, **session
  count is**.
- **`dcg` blocks `rm -rf` and shell redirects to variable paths.** Use literal paths, and
  `git rm` for deletions.
- **The suite cannot catch launch-path bugs.** Every test injects a fake spawn, which is
  exactly why six launch bugs survived 378 green tests. If sessions return, at least one
  test must exercise the real subprocess.

## Decisions made this session

- **The four layers stay; the driving was the problem.** The run produced genuinely
  implementable spec — `S·06` derived a security property (a refusal body must leak
  nothing) three layers down from a behaviour. The layers earn their keep.
- **mu-spec is a pure tool unit.** No agents, ever. The boundary that moved when it drove
  itself has moved back.
- **Three levels, three stores, three lifetimes** — wayfinder tickets (any domain,
  Taskwarrior), mu-spec entries (one project's spec, forever), GitHub issues (dev work,
  post-pipeline). Do not merge them; an earlier proposal to fold tickets into mu-spec's
  issue queue was wrong and was rejected.
- **One map per layer.** The destination carries the layer, and a finished map's
  decisions are the next map's context. The descent is not automated.
- **A processing unit executes ratified work and never decides what it should be** —
  proposed, to be ratified next session.
- **Repair batching and the stall signature were fixed but never re-run.** One repair
  session now takes every open batch; the stall signature is the work unit rather than its
  contents. Both are untested against a live run.
