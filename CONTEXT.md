# CONTEXT — mu-spec (specification memory unit)

Inherits: [holonic node system](../../CONTEXT.md). Terms defined there are used as
defined there and not restated here.

This unit owns the **specification pipeline** vocabulary: layer, entry, identifier,
derives-from, slice, spine, cross-cutting, admission gate, judgement gate, conflict
gate, amendment, upward reconciliation, blast radius, write set, read set.

None of it is glossarised yet, but it is the most fully reasoned vocabulary in the
system and the definitions already exist in prose: the origin doc
[`../../docs_crystalising_unit_idea.md`](../../docs_crystalising_unit_idea.md) and
[`docs/DESIGN.md`](./docs/DESIGN.md).

A note on why this matters more here than elsewhere, from
`~/.claude/skills/domain-modeling/SKILL.md`: a specification is a graph of derived
claims, and every layer inherits the words of the one above. Vocabulary drift is the
cheapest way to produce a graph that passes every gate and means nothing.

Promote a term here when it is resolved, one at a time, never batched. Format:
`~/.claude/skills/domain-modeling/CONTEXT-FORMAT.md`.
