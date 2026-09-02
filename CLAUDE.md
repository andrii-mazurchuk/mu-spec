# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read this first

**This repository is developed in isolation.** It is one unit in a holonic
system of independent units. You must not read, open, clone, or reason about
any other unit's source — not to copy a pattern, not to check how they did
it, not "just to look." Units are interchangeable by design; every glance at
a sibling's internals is how coupling gets introduced between things that are
supposed to be swappable.

Everything you need to know about the system is written below. It is
deliberately self-contained: there is no parent doc to go fetch, and you
should not go looking for one. If something genuinely isn't covered here,
say so and ask — don't go read another repo to find out.

The gateway repo (`holonic-node`) owns the canonical version of the standard
reproduced below. If you ever find the two disagree, the gateway's
`docs/UNIT_STANDARDS.md` wins and this file is the one to fix.

## What this unit is

**mu-docs** — a memory unit. `unit_type: memory`, `lifecycle: persistent`.

It owns per-project documentation: the authoritative, structured description
of what each project is and what it should do. Documentation here is not a
description of the code written after the fact — it is the source the work
gets driven from. A change to a project's documentation is the intended way
to request a change to that project.

**Sibling memory units, for context on what this is not.** The system already
has two other memory units, and the boundaries matter: one holds best-effort
keyword-matched agent memory (optional, lossy by design), another holds
structured session and judgment logs (required infrastructure, append-only).
mu-docs is a third: authoritative, versioned, human-and-agent-editable
project documentation. Different durability guarantees, different consumers.
That is why it is its own unit rather than an endpoint on an existing one.

### The boundary you must not cross

**mu-docs stores and serves. It never executes.**

It does not launch sessions, does not run `claude -p`, does not modify any
project's code, does not decide when work should happen. Exactly one unit in
this system launches agent sessions, and it is not this one.

What mu-docs does is own the documentation and make change *legible*: what
the docs say now, what changed, when, and what that change implies is wanted.
Another unit reads that and does the work.

If you find yourself writing code that spawns a process, edits a file outside
this unit's own storage, or decides that some project now needs work done to
it — stop. That belongs to a different unit, and putting it here collapses
two responsibilities into one and makes both un-swappable.

## The standard every unit implements

### Four required HTTP endpoints

Implement all four in this unit's own `server.py`. There is no shared base
class and there deliberately isn't one — every unit reimplements these
independently. That small duplication is a decision, not an oversight; do not
try to factor it out or import it from anywhere.

- **`GET /health`** → `{"status": "ok"}`. Boolean liveness only, nothing else.
  This is what the gateway polls on an interval.
- **`GET /stats`** → `{"unit", "computed_at", "metrics": {...}}`. The envelope
  is identical across every unit; `metrics` is unit-specific *mechanical* data
  — counts, sizes, timings. Never judgment, never anything an agent decided.
- **`GET /prompts/<tier>`** → this unit's own self-description as text. At
  minimum a `default` tier. This is what a *peer* learns about mu-docs when
  assembling its own context. Tiers are `default` and `reference`.
- **`GET /tools`** → `{"unit", "tools": [...]}`, each entry
  `{name, description, method, path, input_schema}`. Hand-written, not
  generated from the routing table — every entry needs a description written
  for a model to read. Excludes `/health` and `/tools` themselves.

Tool names are action-style, not path echoes. One `/documents` route served
under both GET and POST is declared as two tools, `create_document` and
`query_documents`.

**How a tool declaration maps to a real call.** A property whose name appears
literally as `{name}` inside the tool's `path` is a path substitution —
removed from the URL template, never sent as a query or body param.
Everything else goes in the query string for `GET`, or the JSON body for
`POST`. A non-JSON 2xx response is returned to the caller as a raw string,
which is why `/prompts/<tier>` can return plain text.

### Optional endpoints

- **`POST /trigger`** — if this unit declares a schedule, the gateway pokes
  it here rather than restarting the process. Implement only if mu-docs needs
  to do periodic work of its own.
- **`POST /inbox`** — the convention for accepting pushed work from a peer.
  The gateway never calls this.

### Non-negotiable invariants

- **Enclosure is a process boundary.** Everything mu-docs owns is reachable
  only through its HTTP API. No other unit may read this unit's storage
  directly, and this unit must never read another's. If code outside a unit's
  repo touches a path inside its private storage, that's the bug.
- **Never name another unit in code.** Peers are discovered at runtime from
  `peers.json` (see below) and addressed by their registry name from there.
  A hardcoded unit name makes that unit un-renameable and un-swappable.
- **A call to another unit must never depend on a third unit being up.**
  Check this explicitly for every cross-unit call you add.
- **Everything degrades, nothing crashes.** A missing optional input, an
  unreachable peer, a malformed config file — all resolve to an empty or
  default value, never an exception that reaches a request handler. Absence
  is a normal, expected state throughout this system. Match that discipline.
- **Config comes from `os.environ`.** The gateway assembles this unit's
  environment and injects it at process start. Read env vars normally. Do not
  invent a config-file convention.

### Files the gateway writes into this directory

Generated before the process starts, recomputed on every gateway start. Read
them; never edit them, never commit them.

- **`peers.json`** — every registered unit's `name`, `base_url`,
  `unit_type`, `capabilities`, `prompts`. Flat and unfiltered; decide locally
  which peers matter. Reading it never requires anything to be reachable.
- **`cost_policy.json`** — this unit's own cost/usage-cap values. Always
  written, possibly `{}`, which means "enforce nothing."
- **`delivery_policy.json`** — resolved `owner`/`logs`/`thinking` notification
  targets, for addressing a message by role instead of by unit name.

### How to reach another unit, when you need to

1. **Direct HTTP** to a peer's `base_url` from `peers.json`. The default.
2. **`POST /route` on the bridge** — for a call that should be centrally
   audited, or addressed to a role (`owner`/`logs`/`thinking`) instead of a
   unit. Returns `{delivered, reason}`; never raises.
3. **MCP tools** — only relevant inside an agent session, not for this unit's
   own code. Machine-to-machine calls stay plain HTTP.

mu-docs is a store. It should need very few outbound calls; be suspicious of
any design that needs many.

## Stack and commands

Python ≥3.10, stdlib-first. `pytest` only for tests — no other test
dependency, no framework beyond what's already declared.

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_server.py::test_health_returns_ok   # single test
python -m mu_docs.main                                # run the server
```

CI runs exactly `pip install -e ".[dev]"` then `pytest`. No linter or
formatter is configured — don't add one unprompted.

## Working style here

- **Test-first for anything non-trivial.** Write the failing test, watch it
  fail, then implement. The suite is the contract.
- **Dependency-inject anything that touches the outside world** — clocks,
  HTTP calls, the filesystem root — as constructor or function arguments,
  never module-level globals. This is how every unit in this system is
  testable without sockets or sleeps, and it is expected here.
- **Storage stays behind one module.** Whatever backs the document store,
  exactly one module may touch it; every other caller goes through that
  module's functions. This is what makes the backing store swappable later.
- Prefer the standard library. Prefer deleting over adding. Don't build
  configuration for a value that never changes.
