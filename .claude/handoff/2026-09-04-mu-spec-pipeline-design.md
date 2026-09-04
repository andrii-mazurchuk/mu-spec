# Handoff — 2026-09-04 — mu-spec: pipeline built, agent design settled, cross-cutting contested

> Previous handoff: none — first session

## Session summary

This session did two things. First it built **mu-spec**, a new memory unit that holds a project's
*derivation graph*: a five-layer specification (intent → behaviour → architecture → spec → code)
where every entry declares what it derives from, so the blast radius of any change is computable
rather than guessed. That part is real, tested, and runs end to end.

Second — and this is where most of the session went — it worked through the **design of the agent
that will actually drive the pipeline**. Nothing derives anything today; every entry in the demo was
hand-written. The design conversation covered where the agent lives, how sessions are scoped, how
slices work, and how changes get in. Scope A (the agent) is closed. Scope B (slices) is closed
*except* cross-cutting, which Andrey pushed back on and which is the first thing to resolve.

Also in this session, unrelated to mu-spec: a gateway fix was shipped as PR #1 on `holonic-node`
(splitting the `peer` flag from `health_check` and making baseline unit names configurable). It is
open and awaiting Andrey's approval.

## What's done

- **mu-spec exists** at `C:\agents\units\mu-spec`, own git repo, 130 tests passing, 5 commits.
  Remote does not exist yet — nothing pushed.
- **The graph** — permanent identifiers (`B·14`), edges, ancestors/descendants, blast radius, spine.
- **Storage** — markdown entry files one-per-slice-per-layer, JSON manifest holding slice membership
  (a *set*, never a range) and identifier high-water marks so an id is never reused.
- **The inbox** — the single external door. Five request types (`initiate`/`feature`/`correction`/
  `comment`/`question`); the *type* decides how deep a change may originate, so there is no way to
  ask for a spec edit directly.
- **Amendments** — the pipeline's write path. Atomic, gate-validated, and every one must cite the
  inbox request that authorised it.
- **Two gates** — *sound* (no orphans; blocks writes and work packages) and *complete* (nothing
  unserved; reports only).
- **Work packages** — bounded context for a coding agent: editable spec entries, justification
  chain, read-only dependencies, and the audit rule.
- **`walkthrough.py`** — runs the entire pipeline and prints every stage. Runs in-process by default
  (no socket), `--http` to bind a port.
- **Design artifact** published (see references) covering layers, sessions, slices, storage, tools,
  and what is built vs designed.
- **Three subagent investigations** completed into the rest of the system's standards: cost policy,
  agentic logging, and session-type patterns. Findings are folded into the design below.

## Current state / open threads

**mu-spec is a skeleton with no muscle.** Every agent session described in the design is unbuilt.
Concretely missing: all five session types, the slicing metrics, cross-slice checks, gate 3
(undeclared dependencies), dependency declaration and processing order, the holding file for
behaviour that has no slice yet, one-slice-per-entry *enforcement* (today `slice_of` returns the
first match and would silently mislead), loop detection, coverage-gap detection, session logging,
cost limits, and session timeouts.

**Andrey's open objection — resolve this first.** The proposal on the table is that `cross_cutting`
becomes a *flag* on a slice rather than one bucket named `cross-cutting`, so you can have `auth`,
`logging`, `notifications` as three separately flagged slices. He is skeptical, and his reasoning is
sound: **if cross-cutting slices are just ordinary slices, then every slice rule applies to them**,
and three of those are hard —

1. **Hierarchy among cross-cutting slices.** Can one depend on another? `auth ↔ notifications` is a
   plausible real loop (auth needs to send alerts; notifications needs to know who the user is), not
   a contrived one. If they can depend on each other the loop check has to run there too.
2. **Upward pressure.** A normal slice needs something new from a cross-cutting one but is forbidden
   from writing to it. Who initiates that change, and through what path?
3. **Re-derivation.** Cross-cutting sits underneath everything, so almost any change could touch it.
   What actually triggers propagating it again is a different question from every other slice.

The rule offered as protection — *a cross-cutting slice defines the mechanism, never the content* —
helps but does not close these. Possible outcomes: keep the flag model and add rules for the three;
go back to one bucket and accept the lost precision; or find a third shape where cross-cutting is
genuinely not a slice at all.

**Scope C is unopened.** The slicing metrics, and the harder question underneath: how do you tell
whether a slicing is any good? Andrey wants to trial several approaches, which needs something to
score them against, and there is no answer key yet.

## ▶ Next step

**Resolve cross-cutting.** Work the three objections above in order — hierarchy first, since whether
cross-cutting slices may depend on each other decides most of the rest. Do not write code for it
until Andrey accepts a shape; the last two sessions he explicitly asked to be consulted before
implementation and was right to.

Then, in order: close scope C (slicing metrics + how to evaluate a slicing), then start building the
session infrastructure.

## Files & references to read

**Read these first, in this order:**

- **The design artifact** — https://claude.ai/code/artifact/af605511-76e5-4bad-bd17-787b5ffb1069
  The whole pipeline drawn out in diagrams. Section 9 "Current open item" is live and holds the
  cross-cutting objection. Andrey reads visually and asked for this specifically; update it as
  decisions land (republish the same file path from the scratchpad, or pass the URL).
- `C:\agents\units\mu-spec\docs\DESIGN.md` — Andrey's own architecture doc for the layered
  specification pipeline. The source of truth for intent. §4 covers slices, §5 storage, §6 gates,
  §9 upward reconciliation, §11 what he flagged as undesigned.
- `C:\agents\units\mu-spec\CLAUDE.md` — self-contained: the system standard is inlined so a session
  here never needs to read the gateway or a sibling unit.

**Then the code, if implementing:**

- `mu_spec/service.py` — the operations. `submit_amendment` holds the origination-depth rule and the
  stranded-vs-orphan partition, which are the two subtlest pieces.
- `mu_spec/gates.py` — sound vs complete, and why the layer boundaries are fixed not computed.
- `mu_spec/storage.py` — the only module touching the filesystem. `split_slice` shows how membership
  moves without renumbering.
- `mu_spec/inbox.py` — request types and the `originates_at` permission table.
- `walkthrough.py` — run it (`python walkthrough.py`) before changing anything; it is the fastest way
  to see the whole system behave.

**Not in this repo:**

- `C:\agents\holonic-node` — the gateway. PR #1 open and awaiting approval. Do not work on it from a
  mu-spec session; one unit per session is a hard project rule.

## Gotchas / warnings

- **Do not read other units' source.** `.claude/settings.json` in this repo denies Read/Edit/Write on
  `../**` to enforce it. When you need to know how another unit solved something, **spawn a subagent
  to investigate and report the pattern** — that is what was done three times this session and it
  works well. It keeps their implementation out of your context, which is the whole point.
- **`entry_type` in mu-logs is a closed vocabulary** — `session_run`, `judgment`, `cap_check`,
  `system_message`. Anything else is a 400. mu-spec cannot invent one; adding a type is a standards
  change in another repo.
- **The `session_run` payload has nine baseline fields spelled identically across three units**, plus
  exactly one unit-defined scope field. mu-logs' rollup reads `outcome`, `duration_seconds` and
  `cost` by exact name — a misspelling silently drops the entry from budget totals.
- **`GET /search` in mu-logs indexes only `payload["judgment"]`.** Reasoning goes in that exact key
  or it is not findable.
- **Registering the MCP bridge without granting `mcp__mcp-bridge__*` in `--allowedTools` makes every
  bridge call hang forever** on a permission prompt nothing can answer in `-p`. This shipped broken
  once elsewhere and protocol-level tests missed it. Test headless end to end.
- **No unit sets a session timeout anywhere.** That is a real gap, not a convention to inherit.
- **Sessions return nothing to their parent.** The established pattern is: the session posts
  structured output via a named MCP tool, and the parent does a post-session read-back with a
  freshness boundary (timestamp captured immediately before launching). State commits only on exit 0.
- **The bash heredoc in this environment mangles `\n` escapes** inside Python. Write patch scripts to
  a file and run them, or use the Edit tool.
- **`dcg` blocks all file deletion** — `rm -rf`, `shutil.rmtree`, `os.remove`. Ask Andrey to delete
  things manually. There is a stale `mu_docs.egg-info/` in the repo root from before the rename that
  still needs removing (it is gitignored, harmless).
- **Andrey wants to be consulted before implementation.** He said so twice. Propose, get agreement,
  then build.
- **Keep the vocabulary plain.** He asked explicitly: "approval step" not ratification, "cross-slice
  check" not reconciliation, "conflict" not contradiction. Jargon was actively slowing him down.

## Decisions made this session

**The agent lives inside mu-spec.** Putting it in a separate unit would force that unit to know what
a layer means and how slices are cut — semantic coupling, invisible to the gateway, worse than the
alternative. (Andrey's call; the earlier position that "the unit computes, never reasons" was mine
and was wrong — it hardened an observation into a law.)

**`unit_type` stays `memory`.** The agent only ever works on the memory the unit was given.

**Sessions are headless Claude Code**, one per layer transition, never touching more than two layers.
Scoped per intent entry for I→B (no slices exist yet) and per slice below that.

**Five session types:** handle an inbox item · propagate · propose slicing · cross-slice check ·
back-check. A *review* session type was considered and cut — cross-slice check covers it, and
`review_layer` remains as a plain endpoint needing no model.

**One human stop, at slice approval**, between behaviour and architecture. Everything else runs
unattended; cross-slice checks stop the run only on a loop or conflict, never for routine bookkeeping.

**Slice dependencies are proposed at slicing time**, not discovered afterwards — otherwise there is
no order to propagate in. The cross-slice check becomes verification, not discovery.

**Slices propagate in dependency order**, with same-level slices free to run in parallel. Build
serial first; allocate identifiers under a lock so parallel is a later switch, not a rewrite.

**A loop between slices means the cut was wrong**, not that scheduling failed. Detect it while it is
still a proposal, where merging candidates is free — "slices never merge" applies to slices that
exist, and a proposal is not one yet.

**One entry belongs to exactly one slice.** Slices define write ownership; overlapping ownership is
no ownership, and it makes the work-package audit unenforceable because two write sets would overlap.
This needs *enforcing* — today it is only assumed.

**Approval arrives as plain words through the inbox** (a new `decision` type), because Andrey will be
answering from a chat window, not editing a form. Iterating is cheap: slices only harden once
something derives from them.

**The interview lives in the communication unit, not here.** mu-spec instead owns the *contract* for
what an `initiate` request must contain. `prompts/reference.md` currently scaffolds an interview and
should be rewritten as that contract.

**Adopt the system's existing standards wholesale** rather than inventing: cost policy read-and-
degrade with fail-open, `session_run` logging shape, `judgment` entries for what an agent could not
derive mechanically (this already exists as a standard and answers the "judgement gate" question),
lockfile serialisation, and scoping-by-omission as the guardrail.

**Gates split into two axes** — *sound* blocks, *complete* reports. Blocking on both would make every
legitimate propagation illegal, since a half-propagated layer is always incomplete. Blocking on
neither makes the gate decorative.

**Superseding an entry strands its children, and that is allowed.** Those stale references are
reported as `stale_references` plus a blast radius, and the graph stays unsound until they are
re-derived — which is what withholds work packages meanwhile. Refusing them would make corrections
impossible.
