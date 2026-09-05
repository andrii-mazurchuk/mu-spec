# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read this first

**This repository is developed in isolation.** It is one unit in a holonic
system of independent units. You must not read, open, clone, or reason about
any other unit's source — not to copy a pattern, not to check how they did
it, not "just to look." Units are interchangeable by design; every glance at
a sibling's internals is how coupling gets introduced between things that are
supposed to be swappable.

Everything you need is in this repo. The system standard is reproduced below;
this unit's own domain design is `docs/DESIGN.md`. There is no parent doc to
go fetch. If something genuinely isn't covered, say so and ask — don't go
read another repo to find out.

The gateway repo owns the canonical standard reproduced below. If the two
ever disagree, the gateway's `docs/UNIT_STANDARDS.md` wins and this file is
the one to fix.

## What this unit is

**mu-spec** — a memory unit. `unit_type: memory`, `lifecycle: persistent`.

It owns the **derivation graph** of a project: the layered specification that
agent-built software is derived from, and the edges proving what derives from
what. Read `docs/DESIGN.md` before writing any code here — it is the full
architecture, and this section is only the orientation.

Five layers — intent, behaviour, architecture, implementation spec, code —
each entry carrying an identifier, a derives-from list (vertical, exactly one
layer up), a depends-on list (horizontal, within its layer), an emits-into
list (horizontal, only into a cross-cutting slice), and a body.
Those structural fields turn a pile of records into a directed graph, and that
graph is the entire point: it makes the blast radius of any change
mechanically computable, so human review can be scoped to the radius instead
of to whole documents.

### The boundary you must not cross

**mu-spec computes. It never reasons, and it never executes.**

Every operation this unit performs is deterministic and derivable from the
graph. Anything requiring judgement belongs to a session in another unit,
which calls this one.

| mu-spec owns (mechanical) | Another unit owns (judgement) |
|---|---|
| Storing entries; enforcing identifier permanence and append-only history | Authoring entry bodies |
| Spine generation; retrieval of bodies by identifier | Deciding which entries a task needs |
| Admission gates: orphans, broken same-layer edges, slice cycles, cross-cutting reaching into a feature slice, unserved requirements | Judgement gates: flagging what could not be derived |
| Blast radius; spec-diff → write set / read set; auditing a diff against the declared write set | Classifying a correction to its layer |
| Wave assignment: the order slices may be worked in, from the projected graph | Deciding a slice is worth working at all |
| The issue queue: storing it, grouping it by target slice, computing each batch's re-run scope, escalating what is not a repair | Whether an issue is additive or semantic, and what the actual fix is |
| The raw coupling / direction / ubiquity / size numbers behind slice proposals | Proposing and ratifying the slices |
| Scoring a *proposed* partition without creating it | Grouping behaviours by what they are about |
| Recording the lifecycle: requests as worded, corrections and the layer they entered at, refusals, assumptions | Deciding what an assumption should have been |
| Change locality, correction distribution, cohesion, coupling — reported, never enforced | Whether a slicing is any good |

The tell that this split is right: `docs/DESIGN.md` §6 calls admission gates
"mechanical, run by the agent, human sees only failures." A mechanical check
doesn't need an agent — it needs a function. Putting them here means a
session in a hurry cannot skip them, which is the entire point of a gate.

If you find yourself writing code that spawns a process, edits a file outside
this unit's own storage, calls a model, or decides that some project now
needs work done to it — stop. That belongs elsewhere.

### Invariants this unit is the guardian of

These are not documentation. This unit must **enforce** them, and a violation
is an error the caller sees, not a warning in a log.

- **Identifiers are never reused and never renumbered.**
- **Identifiers encode layer and creation order only, never slice.** Slice
  membership is a property of the manifest. This is what lets a slice split
  without renumbering: a split redistributes membership, and every entry
  keeps the identifier it was born with. A slice's membership is a **set**,
  never a range — never write code that assumes contiguity.
- **Amendments are append-only**, with a superseding marker. Nothing is
  edited in place; history is what makes the pipeline auditable.
- **Slices split, never merge.** Merging destroys identifier locality.
- **No slice ever writes into another.** One entry belongs to exactly one
  slice, and slices define write ownership. This is universal, not a
  cross-cutting rule.
- **Cross-cutting is a slice type, decided by two tests** — does the caller
  branch on what comes back, and does the contract name a domain object
  someone else owns. Both must pass. Fan-in is never the criterion: a slice
  everything depends on is a foundational slice, not a cross-cutting one.
- **Slice dependency is projected from entry edges, never authored.** There is
  deliberately no field to declare it in; two statements of the same fact
  drift, and the authored one goes stale.
- **No metric ever gates.** Gates block on what is definitionally broken — a
  cycle, a dangling edge, an entry with two owners. Everything in `metrics.py`,
  `slicing.py` and `lifecycle.py` is a proxy for a question nobody can answer
  yet, and giving a proxy the authority of a certainty is the worst trade
  available here. They report. If you find yourself writing `if cohesion <`
  anywhere near a refusal, stop.
- **Agents never message each other.** A request from one part of the
  pipeline to another is an issue filed against the target *entry*, and the
  raiser proceeds on a stated assumption. Anything else means blocking or
  nondeterminism, and the audit property is gone either way.
- **An edge into a cross-cutting slice is `emits_into`, never `depends_on`;
  a cross-cutting slice has no outbound dependency into a feature slice.**
  An emission imposes no order, which is what keeps a concern derivable
  before everything that emits into it -- and what makes a cycle involving
  one impossible to express.

### Two decisions already made — do not reopen

- **No vector search, no embeddings, no similarity ranking.** Retrieval is
  graph traversal, which is deterministic, cheap and explainable. This is why
  the unit has zero dependencies; keep it that way.
- **History is never loaded by default.** It exists for reconciliation and
  audit. If history sits alongside live entries, every read pays for every
  past mistake.

## The standard every unit implements

### Four required HTTP endpoints

Implement all four in this unit's own `server.py`. There is no shared base
class and there deliberately isn't one — every unit reimplements these
independently. That duplication is a decision, not an oversight; do not try
to factor it out or import it from anywhere.

- **`GET /health`** → `{"status": "ok"}`. Boolean liveness only.
- **`GET /stats`** → `{"unit", "computed_at", "metrics": {...}}`. The
  envelope is identical across every unit; `metrics` is unit-specific
  *mechanical* data — counts, sizes, timings. Never judgement.
- **`GET /prompts/<tier>`** → this unit's own self-description as text, at
  minimum a `default` tier. What a *peer* learns about mu-spec when
  assembling its own context. Tiers are `default` and `reference`.
- **`GET /tools`** → `{"unit", "tools": [...]}`, each entry
  `{name, description, method, path, input_schema}`. Hand-written, not
  generated from the routing table — every entry needs a description written
  for a model to read. Excludes `/health` and `/tools` themselves.

Tool names are action-style, not path echoes: one `/entries` route served
under both GET and POST is declared as `create_entry` and `query_entries`.

**How a tool declaration maps to a real call.** A property whose name appears
literally as `{name}` inside the tool's `path` is a path substitution —
removed from the URL template, never sent as a query or body param.
Everything else goes in the query string for `GET`, or the JSON body for
`POST`. A non-JSON 2xx response is returned to the caller as a raw string,
which is why `/prompts/<tier>` can return plain text.

### Optional endpoints

- **`POST /trigger`** — if this unit declares a schedule, the gateway pokes
  it here rather than restarting the process.
- **`POST /inbox`** — the convention for accepting pushed work from a peer.
  The gateway never calls this.

### Non-negotiable invariants

- **Enclosure is a process boundary.** Everything mu-spec owns is reachable
  only through its HTTP API. No other unit may read this unit's storage
  directly, and this unit must never read another's.
- **Never name another unit in code.** Peers are discovered at runtime from
  `peers.json` and addressed by their registry name from there. A hardcoded
  unit name makes that unit un-renameable and un-swappable.
- **A call to another unit must never depend on a third unit being up.**
- **Everything degrades, nothing crashes.** A missing optional input, an
  unreachable peer, a malformed config — all resolve to an empty or default
  value, never an exception reaching a request handler. Absence is normal
  throughout this system.
  *Exception:* the guardian invariants above. A caller trying to renumber an
  identifier or merge a slice gets a hard error. Degrading there would let
  the graph rot silently, which is the one failure this unit exists to make
  impossible.
- **Config comes from `os.environ`.** The gateway assembles this unit's
  environment and injects it at process start. Don't invent a config-file
  convention.

### Files the gateway writes into this directory

Generated before the process starts, recomputed on every gateway start. Read
them; never edit them, never commit them.

- **`peers.json`** — every registered unit's `name`, `base_url`,
  `unit_type`, `capabilities`, `prompts`. Flat and unfiltered.
- **`cost_policy.json`** — this unit's own cost/usage-cap values. Always
  written, possibly `{}`, meaning "enforce nothing."
- **`delivery_policy.json`** — resolved `owner`/`logs`/`thinking` targets,
  for addressing a message by role instead of by unit name.

### How to reach another unit, when you need to

1. **Direct HTTP** to a peer's `base_url` from `peers.json`. The default.
2. **`POST /route` on the bridge** — for a centrally audited call, or one
   addressed to a role. Returns `{delivered, reason}`; never raises.
3. **MCP tools** — only inside an agent session, not this unit's own code.

mu-spec is a store. It should need almost no outbound calls; be suspicious of
any design that needs many.

## Stack and commands

Python ≥3.10, **stdlib only**. `pytest` for tests, nothing else. The
no-embeddings decision is what keeps this true — don't add a dependency
without a real argument.

Modules, and the one-line reason each exists:

| Module | Owns |
|---|---|
| `identifiers` | Parsing, ordering, and what "one layer up" means |
| `graph` | Entries, the three edge kinds, traversal, the computed spine |
| `storage` | The only module that touches the filesystem |
| `gates` | Checks answerable from the graph alone — knows nothing of slices |
| `slice_gates` | Checks needing the manifest: cycles, ownership, edge kind |
| `waves` | Execution order by longest path |
| `inbox` | The single external door; type-is-permission |
| `issues` | The internal queue — one part of the pipeline asking another |
| `reconcile` | Routing that queue into repair batches, headers only |
| `planning` | Spec diff, write/read set, the git-diff audit |
| `lifecycle` | What happened, in order — what the graph cannot recover |
| `metrics` | Change locality, corrections by layer, cohesion. Never gates |
| `slicing` | Candidates, and scoring a proposal that commits nothing |
| `shipping` | A best-effort copy of each event to whoever holds the logs role |
| `service` | The operations, as plain functions over a store. No HTTP |
| `server` | Routing and the tool manifest. The only module that knows HTTP |

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_server.py::test_health_returns_ok   # single test
python -m mu_spec.main                                # defaults to :9006
```

CI runs exactly `pip install -e ".[dev]"` then `pytest`. No linter or
formatter is configured — don't add one unprompted.

## Working style here

- **Test-first for anything non-trivial.** Write the failing test, watch it
  fail, then implement. The suite is the contract.
- **Dependency-inject anything touching the outside world** — clocks, the
  filesystem root, HTTP — as constructor or function arguments, never
  module-level globals. Every unit in this system is testable without
  sockets or sleeps, and this one must be too.
- **Storage stays behind one module.** Exactly one module touches the backing
  store; every other caller goes through its functions. That is what makes
  the store swappable later.
- **The graph core needs only `id`, the edge lists, and `body`.** Per-layer field
  shapes are still undesigned (`docs/DESIGN.md` §11) and constrain only what
  goes *inside* a body. Don't block on them, and don't bake a layer's fields
  into the graph layer.
- Prefer the standard library. Prefer deleting over adding. Don't build
  configuration for a value that never changes.
