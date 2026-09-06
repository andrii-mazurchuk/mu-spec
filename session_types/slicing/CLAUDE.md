# Slicing session

@../SHARED.md

**The shared contract above is part of your instructions.** This file only covers what is
specific to slicing.

## Why you are running

Behaviour entries exist that belong to no slice. Nothing below them can be
derived, because there would be no slice to own the result. Your dynamic
prompt lists the unsliced identifiers.

You run **once**, between behaviour and architecture. Slicing on intent
prose gives the wrong cuts; slicing later leaves architecture with nowhere
to live.

## What a slice is

A vertical cut through the system by capability — `listings`, `discovery`,
`payouts`. It owns its behaviour, architecture and spec entries.

**A slice is a set of identifiers, never a range.** Identifiers encode layer
and creation order only, so a slice's members are scattered by construction.
Never assume contiguity.

**Every entry belongs to exactly one slice.** This is not tidiness — the
slice decides who may edit what. Two owners means two coding agents both
receive the same entry as editable, both change it, neither knows, and the
audit passes for both.

## What you produce

A **proposal**, and nothing else. You commit nothing.

1. `propose_slicing` gives you the raw numbers — coupling, direction,
   ubiquity, size. Those are inputs, not answers.
2. Read the behaviour bodies and group them **by what they are about**. This
   is the judgement you own and the numbers cannot make it for you.
3. Propose a **type** for each group alongside its membership (below).
4. `score_slicing` scores the partition without creating it. A bad score is
   information, not a verdict — a slicing can score badly and still be the
   right cut.
5. **`submit_proposal`.** This is the step that makes your work survive.

Submitting stores the proposal for a human to rule on, and stops another
slicing session being dispatched over the top of it. **A session that only
reports its proposal in its final message has produced nothing** — the
process exits, the text goes nowhere, and the next trigger dispatches
slicing again on identical input. That has already happened once.

Send the membership, the per-slice types, and a `note` carrying your
reasoning — the two-test results, the warnings you are not resolving, and
anything a human needs in order to disagree with you.

Check `get_proposal` first. If a previous proposal was rejected, its reason
is there, and it is the most useful thing you will read this session.

A human ratifies. Only then does the partition become real. Do not create
slices, and do not treat your own proposal as settled.

## Feature or cross-cutting: two tests, both must pass

A slice is cross-cutting when:

1. **The caller does not branch on what comes back.** If the caller
   inspects the result and takes a different path, the slice is returning a
   decision the caller depends on — that is a dependency, not a concern.
2. **The contract names no domain object someone else owns.** If it takes a
   `User` or an `Order`, it is entangled with the slice that owns that
   object.

**Fan-in is never the criterion.** A slice everything depends on is a
*foundational* slice, not a cross-cutting one. This is the mistake to avoid:
"lots of things use it" is not evidence of anything.

Worked check: `auth` returns allow/deny and the caller branches on it — test
1 fails, so it is an ordinary slice. `notifications` takes a recipient user,
which accounts owns — test 2 fails, so it is an ordinary slice too.

The edge rules follow from the same fact seen twice: an edge **into** a
cross-cutting slice is `emits_into`, never `depends_on`, and a cross-cutting
slice has **no outbound dependency into a feature slice**. If a concern
needs to ask a feature slice for something, it needs to know its caller —
which fails test 1, so it was never cross-cutting.

## What you must not do

- **Do not create slices.** You propose; a human ratifies.
- **Do not author or edit entries.** Not one, not to make a group tidier.
- **Do not declare slice dependencies.** There is deliberately no field for
  them: slice dependency is **projected** from the entries' own edges. Two
  statements of the same fact drift, and the authored one goes stale.
- **Do not group by shared nouns or keyword overlap.** Text analysis is not
  what this is. Read the bodies and decide what they are *about*.
- **Do not rule on your own proposal.** Whether a slicing is any good is not
  yours to settle, and no metric gates it.
- **Do not merge slices.** Slices split, never merge — merging destroys
  identifier locality. If a cut turns out too coarse, it splits later, and
  every entry keeps the identifier it was born with.

## Coverage gaps

While reading behaviour you will notice things the layer never mentions but
obviously needs. **Flag them** — `raise_issue` against the nearest entry, or
report them alongside the proposal. Do not write them yourself: behaviour is
not yours to author, and a gap is a finding for triage.
