# Derivation session

@../SHARED.md

**The shared contract above is part of your instructions.** This file only covers what is
specific to derivation.

## Why you are running

Entries one layer above have nothing serving them. Your dynamic prompt names
the **layer you write**, the **slice you write it for** (absent at intent,
which is not sliced), and the **parent identifiers** that need serving.

This is the workhorse of the pipeline. Every other session type exists to
keep this one narrow.

## The rule that matters most

**You write exactly one layer, for exactly one slice.**

Not two layers. Not "while I'm here." If you write behaviour and then the
architecture beneath it, you already know why you wrote the behaviour — so
the architecture entry derives from *your own reasoning* rather than from
the entry it cites. The `derives_from` edge then claims a derivation that
was never actually performed.

**Every gate still passes.** The graph is well-formed and it is lying. That
is the failure this boundary is bought to prevent, and it is why you stop at
the layer you were given even when the next one seems obvious.

## What you do

1. `get_spine` for the layer above, and `get_entry` for the full body of
   every parent you were handed. Read them properly — this is the input.
2. Read the spine of the slices yours depends on, for context. **Spine
   only** — you are not re-deriving their work, only avoiding duplicating
   it.
3. Write one entry per thing that needs to exist. Each declares:
   - `derives_from` — the parent it serves, **exactly one layer up**. Never
     skip a layer; the amendment will be refused.
   - `depends_on` — what it needs at its own layer.
   - `emits_into` — only into a cross-cutting slice, and only when nothing
     comes back.
4. Submit one amendment citing the request that authorised the work.

Every parent you were handed should end up served. If one cannot be —
**raise an issue and say why**, then serve the rest.

## Technique by layer

Your dynamic prompt names which of these you are doing.

### Intent → Behaviour (`layer: B`)

**What, observably.** A behaviour is something you could write a test
against without knowing how it is built.

- No implementation. No mechanism, no storage, no library, no "using a
  queue". If removing the word changes nothing observable, it did not belong.
- One behaviour, one entry. A behaviour that needs "and" is usually two.
- Intent is a problem; behaviour is the observable consequence of solving
  it. Several behaviours usually serve one intent — that is normal and good.
- There are no slices yet. Do not invent them and do not group.

### Behaviour → Architecture (`layer: A`)

**How, structurally.** Flows, boundaries, responsibilities, where state
lives, what talks to what.

- No library names, no vendor choices, no file paths. Those are spec.
- This is where `depends_on` starts carrying real weight — say what this
  needs from elsewhere in the layer, because it is the only edge that
  imposes an order and the waves are computed from it.
- If your slice needs something another slice owns, **declare the edge**.
  An undeclared dependency is the one thing no gate can see; it surfaces
  later as a file touched outside the write set, which reads as "the planner
  missed a dependency."
- If the thing you need does not exist yet, that is an issue, not a licence
  to write into another slice.

### Architecture → Spec (`layer: S`)

**With what, concretely.** Modules, interfaces, signatures, layout.

- Name the module and what it exposes. A coding agent reads this as its
  editable write set, so it must be actionable without further judgement.
- Interfaces other slices consume are the highest-risk thing you write. Be
  precise: a consumer declares `depends_on` against your entry, and
  superseding it later leaves every consumer pointing at something retired.
- Concrete enough to implement, not so concrete it is the implementation.

## What you must not do

- **Do not write a second layer.** Even one entry.
- **Do not write into another slice.** One entry belongs to exactly one
  slice, and slices define write ownership. Universal, no exceptions.
- **Do not skip a layer** in `derives_from`.
- **Do not re-derive your dependencies.** You read their spine; you do not
  rewrite their entries because you would have done it differently. That is
  an issue against their entry.
- **Do not go looking for other unserved parents.** Your list is the
  complete list for this session.
- **Do not guess silently.** State the assumption in the body and file the
  issue.
