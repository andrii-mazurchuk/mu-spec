# Unit contract

mu-spec is a unit in a holonic-node harness. The standard every unit
implements — the HTTP contract, the `units.yaml` manifest, encapsulation
rules — is defined once, canonically, in the gateway repo:
[`holonic-node/docs/UNIT_STANDARDS.md`](https://github.com/lainiwakuraagent-lgtm/holonic-node/blob/main/docs/UNIT_STANDARDS.md).
This file only says what's specific to mu-spec.

## What mu-spec implements

The standard four endpoints (`/health`, `/stats`, `/tools`,
`/prompts/<tier>`) in `mu_spec/server.py`, plus `GET /skills`.

Three prompt tiers rather than the minimum one: `default` orients a peer,
`reference` is the authoring contract, and `wayfinding` is how to run an
effort that fills the graph in. `/skills` names the skills such an effort
expects — `wayfinder` and the ones it calls — and declares plainly that none
of them ship from here. Skills have no sharing standard in this system yet,
and a unit that started shipping them would be setting that standard by
accident. `unit_type: memory`,
`lifecycle: persistent`. No `/trigger`: this unit does no scheduled work.

## The dashboard

**mu-spec serves the page tier, and that required sign-off.**

`GET /dashboard` returns `text/html`: one self-contained file drawing a
five-column derivation graph, served to a human in a browser and framed
below the node's chrome. The bar for this tier is *inexpressible in the
node's panel vocabulary*, not *inconvenient* — see the gateway repo's
`docs/UNIT_STANDARDS.md`, "Taking the page tier requires the standards
owner's sign-off", which is where the rule and the procedure live. A
derivation DAG clears that bar, because no panel kind renders five layers
with same-layer dependency edges between them. Almost nothing else in this
unit does.

**That decision is made and does not need remaking.** What it obliges a
future session here is narrower, and easier to lose: the standard's seven
page-tier requirements apply to **every change to `dashboard.html`**, not
just to the first one.

Below is not that list — the canonical seven live in the gateway's
`UNIT_STANDARDS.md` and restating them here would be a second copy that
drifts. This is what is easiest to lose in *this* page, which is a mix of
those requirements and two traps that are not in them at all.

- Zero absolute fetches. Every path relative, none starting with `/` — an
  absolute one reaches the *node*, which answers with plausible JSON of the
  wrong shape rather than an error.
- No external resources. One file, inline CSS and JS, no build step.
- The `holonic-tokens` opt-in and contract token names, so the node's
  injected palette wins when framed and the page still works standalone.
- `aria-pressed` and every selected state derived from **current** state,
  never written at construction. Written once at build time, the control
  describes the mount-time selection permanently and a screen reader
  announces the wrong thing with nothing to contradict it.
- Stored text escaped or set through `textContent`, never interpolated as
  markup. Entry bodies and issue claims were written by whoever could reach
  this unit.
- The page proven readable **as installed**, through `importlib.resources`
  and never a checkout path — a checkout-relative check passes exactly when
  the real failure happens.
- Degrades on empty, which is the one that rots silently once the store
  fills and nobody opens the empty case again.

- Same-origin only, and no navigation of its own. The topbar and unit
  switcher belong to the node and never unmount; the rail here is in-page
  scope, not navigation.

The two that are not in the standard's seven — `aria-pressed` and escaping —
are here because they are the two this page got right that a rewrite would
most plausibly get wrong. pu shipped the `aria-pressed` bug.

`tests/test_dashboard.py` asserts these, one test per item.

**Do not propose a `graph` panel kind to the gateway.** It has been rejected
twice, once here and once with pu, for the same reason each time: a kind
general enough for every unit's graph expresses none of them well, and
adding one for a single unit shapes every other unit's dashboard around that
unit's problem.

The page is deliberately absent from `/tools`: a model offered it would
fetch HTML in place of the data behind it.

`/stats` carries **already-processed** aggregates — counts per layer, how
many projects are sound, mean change locality, where corrections entered —
with no text and no per-item detail. An analytical unit reads it without
knowing anything about how this unit works. Everything richer is a
registered tool, reachable by an agent rather than by a scrape.

There is no `/metrics` endpoint: the standard defines `/stats` with a
`metrics` field inside it, and that is what this implements.

Everything beyond the four is declared in `/tools` — thirty of them,
covering the inbox, amendments, slice classification, waves, the issue queue
and its reconciliation, module backlinks, planning, the audit, the work
package, the slicing proposal awaiting ratification, and the measurement
surface. Ratifying and rejecting a proposal are routes but deliberately not
tools: a slicing session able to ratify its own proposal would be doing the
one thing its contract forbids. Tool names are action-style rather than
path echoes, so one route served under two methods is declared twice under
two names.

**Logs.** Lifecycle events are pushed to whichever unit holds the `logs`
role, resolved from `delivery_policy.json` at call time and never named in
code, as `entry_type: project_event`. Best-effort in both directions: the
local log is the durable record, and a logs unit that is down, absent, or
that refuses the type changes nothing here.

## Private storage

One directory, learned from `HOLONIC_STATE_DIR`, holding the projects
under `projects/` -- the standard's "Private storage" section. `--root`
and the older `MU_SPEC_ROOT` both still override it, in that order, so an
existing deployment pinning either keeps working untouched.

## What's specific to mu-spec

The derivation graph of a project: a five-layer specification — intent,
behaviour, architecture, implementation spec, code — in which every entry
declares what it derives from. Those edges make the blast radius of any
change mechanically computable, which is what makes review of agent-built
software affordable.

Distinct from the other memory units: one holds best-effort keyword-matched
agent memory, another append-only session and judgment logs. This one holds
an authoritative, append-only, identifier-stable graph, and is the guardian
of the invariants that keep it sound — identifiers never reused or
renumbered, slice membership a set rather than a range, amendments
append-only, slices splitting but never merging.

**It computes; it never reasons and never executes.** Mechanical operations —
admission gates, spine generation, graph traversal, wave assignment,
spec-diff resolution — belong here precisely so a session cannot skip them.
Authoring entries, classifying corrections, ruling a slice cross-cutting, and
declaring what could not be derived belong to whoever calls this one.

It also never runs git. The diff audit takes the touched paths as an
argument; the caller runs git and passes them in.

One rule spans the measurement surface: **it reports and never refuses.**
Gates block on what is definitionally broken. A metric is a proxy for a
question nobody can answer yet, and a proxy must never be given the authority
of a certainty.
