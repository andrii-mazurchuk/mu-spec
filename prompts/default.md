# mu-spec

The memory unit holding the derivation graph of a project: a five-layer
specification — intent, behaviour, architecture, implementation spec, code —
in which every entry declares what it derives from.

Those edges make the blast radius of a change mechanically computable, so
work and review can be scoped to the radius instead of to whole documents.

## Asking it for something

**Everything from outside goes to `POST /inbox`.** You never name a layer
and never write an entry — you say what you want, and the request's `type`
decides how deep the change may reach:

- `initiate` — start a project from a raw idea
- `feature` — something the product does not do yet
- `correction` — something is wrong
- `comment` — an observation attached to part of the design; changes nothing
- `question` — needs an answer, not a change

`targets` is optional and usually omitted. You are not expected to know how
the design is laid out — that is generally why you are asking.

There is deliberately no way to edit the spec directly. Fixing something low
while the layers above still say the old thing is how the artifacts start
lying, so a correction has to enter at intent or behaviour and be carried
down from there.

## Asking it a question

Ask mu-spec for: what an entry says, what derives from it, what it derives
from, which entries a change touches, what order the slices may be worked in,
and whether the graph currently holds together. Load the spine first —
identifier, one-line title, all three edge lists, no bodies — then fetch the
bodies you actually need by identifier.

Two health questions, reported separately. **Sound** means every edge lands
where it should: nothing derives from something missing, retired or out of
layer, no slice cycle, no concern reaching into a feature slice. Unsound
blocks work. **Complete** means knowledge has reached spec on every branch;
incomplete is just the to-do list, and never blocks.

## What it does not do

mu-spec computes; it does not reason and does not execute. It stores entries
and answers questions about the graph. Deciding what a request means,
interviewing whoever sent it, and writing the derived entries all belong to
whoever calls it.

Agents turning a raw idea into intent should fetch `GET /prompts/reference`
first — it carries the intake interview and the rules about what intent
entries may and may not contain.

Identifiers are permanent — never reused, never renumbered — and encode
layer and creation order only, never slice. Amendments are append-only.
Retrieval is graph traversal, not similarity search.
