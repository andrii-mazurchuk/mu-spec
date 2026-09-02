# mu-spec

The memory unit holding the derivation graph of a project: a five-layer
specification — intent, behaviour, architecture, implementation spec, code —
in which every entry declares what it derives from.

Those edges make the blast radius of a change mechanically computable, so
work and review can be scoped to the radius instead of to whole documents.

Ask mu-spec for: what an entry says, what derives from it, what it derives
from, which entries a change touches, and whether the graph currently passes
its admission gates (no orphans, no unserved requirements, no dependency
arrows running backwards).

mu-spec computes; it does not reason and does not execute. It stores entries
and answers questions about the graph. Authoring entries, deciding what a
change means, and acting on it belong to whoever calls it.

Identifiers are permanent — never reused, never renumbered — and encode layer
and creation order only, never slice. Amendments are append-only. Retrieval
is graph traversal, not similarity search.
