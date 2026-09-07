# Running a layered-spec effort

How to fill this graph in, when the way to a finished specification is not
visible yet. The mechanical contract -- what this unit accepts and refuses --
is `GET /prompts/reference`.

This describes **mu-spec's side** of an effort driven by the `wayfinder`
skill. It does not ship that skill, or any of the ones it names below; it
says what this unit expects of them.

---

## What is being built, and why it is layered

A specification in four layers, each answering one question, and every entry
declaring what it comes from:

| layer | answers | and never |
|---|---|---|
| **intent** | *why* -- the problem, in the buyer's own words | how it is solved |
| **behaviour** | *what* -- observable, testable | mechanism, storage, libraries |
| **architecture** | *how* -- flows, boundaries, where state lives | vendor or file names |
| **spec** | *with what* -- modules, interfaces, layout | the implementation itself |

Code is the fifth thing and has no entries: a module declares which spec
entries it implements, and that backlink is the whole of it.

The layers exist so that the blast radius of a change is arithmetic rather
than a guess. That is only true while each layer is derived from the one
above it **by something that actually read it**.

**Documentation- and test-driven.** A spec entry is finished when a test
could be written from it before any code exists. If you cannot see the test,
the entry is not specific enough yet -- and that is a ticket, not a reason to
write vaguer prose.

---

## One map per layer

An effort's **destination** is what fixes its scope, and here the
destinations come from a fixed vocabulary rather than being freeform:

| map | destination |
|---|---|
| 1 | intent for `<project>` |
| 2 | behaviour for `<project>` |
| ↳ | *the slicing, ratified by a human* |
| 3 | architecture for `<slice>` -- one map per slice, parallel within a wave |
| 4 | spec for `<slice>` |

A map is exhausted when nothing is left to decide at that layer. Then chart
the next one; the finished map's decisions are the context for it, which is
cheap to carry because a map is an index rather than a store.

**Do not run ahead of the map.** Deriving a layer whose decisions are still
open produces entries built on guesses, and the guesses will not be visible
afterwards -- the graph will look sound.

---

## Which skill resolves which kind of question

Name these in a map's `## Notes` so every session on that map consults them.
They are not shipped here.

| leaf | skill to call | what it settles |
|---|---|---|
| **grilling** | `grilling`, and `domain-modeling` alongside it | the default. What the person actually means, and what the words mean |
| **research** | `research` | a fact a decision waits on. Runs unattended |
| **prototype** | `prototype` | "how should it behave" -- cheaper to react to a rough thing than to argue |
| **task** | none | the manual work that unblocks a decision |

`grilling` produces no file: its output is shared understanding, and
**recording it here is what makes it durable.** A resolved decision that
never became an entry is a decision the next map cannot see.

`domain-modeling` maintains a glossary of what the words mean. That is
vocabulary and it is complementary -- this graph holds *claims*, not
definitions. Keep them apart rather than duplicating one into the other.

---

## What a resolved ticket does to the graph

A decision is not finished when it is answered. It is finished when it is
**written at a layer**.

1. The request that authorises it goes to `POST /inbox` -- `initiate` for a
   new project, `feature` for something new, `correction` for something
   wrong. One request may authorise many entries over an effort.
2. The entries go to `POST /projects/{project}/amendments`, citing it.
3. What could not be derived becomes an issue against the entry, with the
   assumption stated in the body.

Between maps, `GET /projects/{project}/gates` tells you whether the layer is
sound and what is still unserved. `unserved` is the honest list of what the
next map has to cover.

---

## Fog, and what this unit can and cannot see

This unit computes the gaps it can **name**: an entry nothing serves, a
behaviour no slice owns, spec no module implements. Those are `unserved`,
the slicing rung, and the module backlinks respectively, and they are exact.

It cannot see the gap you cannot yet phrase -- "there are decisions coming in
this area and I do not know what they are". That belongs in the map's fog,
and the test for it is **whether you can state the question now, not whether
you can answer it.**

Both feed the same place. A gap this unit names is a ticket waiting to be
written down; a gap only a person can feel is one that has to be.

---

## The slicing is the one gate a human always holds

After behaviour is complete and before any architecture exists, behaviour is
cut into slices by capability. A slice owns its entries and decides who may
edit what.

Propose with `POST /projects/{project}/slicing/proposal` -- membership, a
type per slice, and a note carrying the reasoning. It creates nothing. While
a proposal is pending, it is the only thing that can happen at that layer.

A human ratifies. Nothing else can: an agent that ratified its own proposal
would have cut the system up on its own authority.

Two tests decide whether a slice is cross-cutting, and **both** must pass:
the caller does not branch on what comes back, and the contract names no
domain object someone else owns. **Fan-in is never the criterion** -- a slice
everything depends on is foundational, not cross-cutting.

Slices split and never merge, so the coarse cut is the recoverable mistake.

---

## The end of the pipeline

When spec is complete for a slice, `GET /projects/{project}/work-package`
returns the bounded context an implementer needs: the write set with full
bodies, the justification above it, and the read set of everything it
depends on, spine only.

That package is what becomes actual development work. This unit does not
create it, run it, or track it -- it holds the specification the work is
derived from, and nothing else.
