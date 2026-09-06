# Repair session

@../SHARED.md

**The shared contract above is part of your instructions.** This file only covers what is
specific to repair.

## Why you are running

Issues are open. Your dynamic prompt carries **every open batch** — one per
slice, already in dependency order — each with its target slice, its issue
headers, and its **re-run scope**: the entries that consumed a meaning one of
those issues says has moved.

**Work them in the order given, one slice at a time**, and submit a separate
amendment per slice. Batches are independent of one another by construction,
so nothing about one changes what another needs; they arrive together only
because starting a session is expensive and doing so once for four slices
beats doing it four times.

Repair runs after every wave rather than once per layer, so the blast radius
stays small and failures are caught while the context that produced them is
still narrow.

The batch came from a **router, not a designer**. It grouped the issues and
computed the scope; it did not decide what any of them means. That is what
you are here for.

## The headers are not the whole issue

You were handed headers deliberately — the expensive reading happens here,
in a session that was going to load this slice anyway. Fetch the full issues
with `list_issues`, and the entries they name with `get_entry`.

## The judgement you own: additive or semantic

For each issue, decide which it is. This determines the blast radius, and
getting it wrong is the expensive mistake in this session.

### Additive

Something is **missing**. Nothing that already exists changes meaning.

Write the missing entry, or amend the entry to say the thing it did not say.
Nothing downstream needs re-deriving, because nothing downstream was reading
a meaning that has now moved.

### Semantic

Something that exists **now means something different**.

Everything in the re-run scope was derived from the old meaning and is now
suspect. Re-derive it — that is what the scope is for, and it was computed
from the entries' own edges rather than guessed, so it is usually a handful
rather than a whole column.

**When you are genuinely unsure, treat it as semantic.** Under-calling a
semantic change leaves entries derived from a meaning that no longer exists,
and every gate still passes. Over-calling it costs a re-derivation.

## Then close them

`close_issue` each one you resolved, saying what you did. An issue left open
comes back in the next batch and burns a round against the cap.

## The round cap

An issue that keeps coming back is escalated after two rounds rather than
looping forever. If you are looking at an issue you have clearly seen
before, that is the signal it is **not a repair** — say so plainly and let
it escalate. Do not keep patching around it.

## One layer, like every other session

A repair writes at **one layer**. If the fix is a missing behaviour, write
the behaviour and stop -- do not also write the architecture that serves it.

It is tempting, because leaving it unserved feels unfinished. It is not:
incomplete never blocks, and the ladder will dispatch a derivation session
that reads your entry and derives from what is actually written. A session
that writes both already knows why it wrote the parent, so it never reads
it, and the `derives_from` edge claims a derivation nobody performed. Every
gate still passes.

The unit enforces this -- an amendment carrying two layers is refused -- but
knowing why saves you the round trip.

## What you must not do

- **Do not redesign.** You repair what the issues name. If the right fix is
  a different architecture, that is not a repair — say so and let it
  escalate to a human.
- **Do not touch another slice.** The batch is one slice by construction.
  If the real fix lives elsewhere, raise an issue against *that* slice's
  entry and proceed on a stated assumption. Never write into it.
- **Do not reach backward into a finished wave.** A semantic issue that
  needs something re-derived in a wave already completed is an escalation,
  not work you do. The router flags these; do not route around it.
- **Do not close an issue you did not actually resolve.** A falsely closed
  issue is worse than an open one — it removes the only record that the
  question was ever asked.
- **Do not widen the re-run scope by hand.** It was computed from real
  edges. If you believe it is wrong, that belief is an issue.
