# Unit contract

mu-docs is a unit in a holonic-node harness. The standard every unit
implements — the HTTP contract, the `units.yaml` manifest, encapsulation
rules — is defined once, canonically, in the gateway repo:
[`holonic-node/docs/UNIT_STANDARDS.md`](https://github.com/lainiwakuraagent-lgtm/holonic-node/blob/main/docs/UNIT_STANDARDS.md).
This file only says what's specific to mu-docs.

## What mu-docs implements

The standard four endpoints (`/health`, `/stats`, `/tools`,
`/prompts/<tier>`) in `mu_docs/server.py`. `unit_type: memory`,
`lifecycle: persistent`.

Its own capabilities are still being designed — see `README.md`.

## What's specific to mu-docs

Authoritative per-project documentation: the structured description of what
each project is and should do, treated as the source work is driven from
rather than as an after-the-fact record. Distinct from the other memory
units — one holds best-effort keyword-matched agent memory, another holds
append-only session and judgment logs; this one holds versioned, editable
project documentation.

**It stores and serves; it never executes.** mu-docs makes documentation
change legible — what the docs say, what changed, and what that implies is
wanted. Deciding to act on that, and doing the work, belongs to the
processing unit. Keeping those apart is what lets either be replaced.
