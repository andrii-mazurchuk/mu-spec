# Unit contract

mu-spec is a unit in a holonic-node harness. The standard every unit
implements — the HTTP contract, the `units.yaml` manifest, encapsulation
rules — is defined once, canonically, in the gateway repo:
[`holonic-node/docs/UNIT_STANDARDS.md`](https://github.com/lainiwakuraagent-lgtm/holonic-node/blob/main/docs/UNIT_STANDARDS.md).
This file only says what's specific to mu-spec.

## What mu-spec implements

The standard four endpoints (`/health`, `/stats`, `/tools`,
`/prompts/<tier>`) in `mu_spec/server.py`. `unit_type: memory`,
`lifecycle: persistent`.

Its own capabilities are being built out — see `docs/DESIGN.md` for the
architecture and `README.md` for current status.

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
admission gates, spine generation, graph traversal, spec-diff resolution —
belong here precisely so a session cannot skip them. Authoring entries,
classifying corrections, and declaring what could not be derived belong to
the processing unit, which calls this one.
