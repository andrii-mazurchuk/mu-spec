# Every session reads this first

You are one session in the mu-spec pipeline. Your own `CLAUDE.md` — the one
already loaded from the directory you are running in — says which type you
are and what you produce. This file is what is true for all six.

## What mu-spec is

It holds the **derivation graph** of a project: a five-layer specification —
intent, behaviour, architecture, implementation spec, code — where every
entry declares what it derives from. Those edges make the blast radius of a
change computable, which is what makes reviewing agent-built software
affordable.

Identifiers look like `B·14`. They encode **layer and creation order only,
never slice**. They are never reused and never renumbered.

Three kinds of edge, and they answer different questions:

- **`derives_from`** — what this serves. Vertical, exactly one layer up.
  Skipping a layer is refused: the layer jumped over can never be reviewed
  and can never be re-derived when the intent changes.
- **`depends_on`** — what this needs. Horizontal, within one layer. The only
  edge that imposes an order.
- **`emits_into`** — what this publishes. Horizontal, only into a
  cross-cutting slice. Fire-and-forget, so it imposes no order at all.

## How you talk to it

**Over HTTP, like any other caller.** The base URL is in your dynamic
prompt.

**Use `curl`.** It is the only network command you are permitted, and the
allowlist is deliberately narrow rather than generous. `python`, PowerShell
and `WebFetch` will not work: the first two are not granted, and `WebFetch`
forces HTTPS and cannot POST, so it can reach neither a local plain-HTTP
unit nor any write endpoint.

**Send a big body on stdin, never as one long `-d` argument.** A command
line of a few kilobytes is truncated by the shell, and a truncated amendment
is a malformed one. Use a heredoc:

```
curl -sS -X POST "$BASE/projects/$P/amendments" \
  -H 'Content-Type: application/json' -d @- <<'JSON'
{ "in_response_to": "msg-0001", "slice": "capture", "entries": [ ... ] }
JSON
```

This matters more than it looks. **One session submits one amendment.** An
amendment is a transaction: it is admitted whole or refused whole, and
splitting one into three because the command line was too long gives you
three transactions, two of which can land while the third is refused. If you
find yourself splitting a write to make it fit, use the heredoc instead.

**Identifiers in a URL** contain `·` (U+00B7). Either the raw character or
its percent-encoded form `%C2%B7` works.

**Never post to `/inbox` to test whether something works.** That door is how
change enters the pipeline: a probe there is a real request that a later
triage session will pick up and act on. Read-only calls are the safe way to
find your footing.

You are running inside mu-spec's own repository. That does not give you a
shortcut: **never read or write `state/`, and never edit the graph on
disk.** Every admission gate lives behind the API precisely so a session in
a hurry cannot skip it. A session that edits storage directly has bypassed
the only thing keeping the graph sound.

Load `get_spine` first — identifier, one-line title, all three edge lists,
no bodies — then fetch the bodies you actually need with `get_entry`.
Retrieval is graph traversal, not search: there is no similarity ranking and
you should not want one.

## Your scope was computed before you started

Whatever your dynamic prompt handed you — a slice, a set of parent
identifiers, an issue batch, a work package — is the complete list for this
session. The wave order, the layer boundary and the ownership rules were all
applied upstream.

**Do not go looking for more work.** Not another slice, not another layer,
not the next thing in the queue. The guardrail is what you were handed, not
your restraint.

## An assumption that is not an issue is a bug

You will hit things you cannot derive: a parent that does not say enough, a
dependency whose contract is ambiguous, a requirement that seems to want
something nobody wrote down.

When that happens:

1. **File it** with `raise_issue` against the entry it is about.
2. **State your assumption** in the entry body you write.
3. **Carry on.**

Never block. Never wait. **Never message another session** — there is no
mechanism for it and there deliberately isn't one, because anything that
blocks makes the pipeline nondeterministic and destroys the audit property.
The raiser proceeds; the router decides later whether it is a repair.

Silently guessing is the failure this rule exists to prevent. An agent that
only ever reports confidence makes every gate downstream theatre.

## Amendments

Every write is an amendment, and **every amendment must cite the inbox
request that authorised it** (`in_response_to`). An amendment nobody asked
for is refused. That is what traces every entry out past the graph to the
person who wanted it.

Amendments are **append-only**. Nothing is edited in place; a superseding
entry carries a marker. History is what makes the pipeline auditable.

## Two gate results you will see

- **Sound** — every edge lands where it should. Unsound **blocks**: your
  amendment is refused. Fix it, don't work around it.
- **Complete** — knowledge has reached spec on every branch. Incomplete
  **never blocks** — it is the to-do list, not a defect. Do not treat an
  `unserved` report as an error.

## Absence is normal

A peer that is not configured is not there. A missing optional input, an
unreachable unit, an empty result — all resolve to a default and none of
them is an error worth stopping for.

## Finish and stop

Do the smallest, safest, most self-contained useful thing your scope allows,
then stop. **Bounded work followed by a clean exit is success, not a
crash.** You are not expected to finish the project.
