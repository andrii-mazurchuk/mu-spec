# Layered specification pipeline for agent-built software

A working architecture for making agent-produced software trustworthy by making the
reasoning that produced it auditable.

---

## 1. Purpose and core claim

The problem is not that agents write bad code. It is that agents write code whose
justification is invisible, so no human can tell a correct implementation of a wrong
decision from an incorrect implementation of a right one.

This system fixes that by requiring that every artifact declare what it derives from.
Code traces to spec, spec traces to architecture, architecture traces to behaviour,
behaviour traces to intent. Nothing exists without a parent. Nothing above exists
without something below serving it.

The result is that a change anywhere has a **mechanically computable blast radius**,
and human review becomes affordable because it is scoped to that radius instead of to
whole documents.

---

## 2. The five layers

| Layer | Answers | Written by | Sliced |
|---|---|---|---|
| Intent | Why — the buyer's problem and constraints | Human, agent-assisted | No |
| Behaviour | What — observable, testable, implementation-free | Agent | Filed after gate |
| Architecture | How — flows, boundaries, data movement | Agent | Yes |
| Implementation spec | With what — libraries, modules, interfaces, layout | Agent | Yes |
| Code | The artifact | Executor agent | Yes |

### 2.1 Intent

The bare requirement as the buyer would state it. This is where requirements *end*
from the human side. It is short by nature and everyone reads it.

### 2.2 Behaviour

The layer most systems omit, and the reason they fail. Intent is too vague to verify
against; architecture is a set of decisions, and decisions can be defensible and still
wrong. Behaviour is the only testable artifact that isn't code.

Stated as: given this input the system does this; this actor can do this and cannot do
that; when this fails the user sees this.

Every acceptance test downstream traces to a behaviour entry. Every architectural
choice must justify itself by naming which behaviours it serves.

### 2.3 Architecture

The agent explaining to its own future sessions what the right structural take is —
data flow, procedure flow, boundaries, standards that bind every later line of code.
Deliberately free of library choices and implementation detail.

### 2.4 Implementation spec

The gap between "data flows this way" and "write this function." Named libraries,
module boundaries, file layout, interfaces. Kept separate from architecture because it
churns fastest, and churn here must not drag architecture with it.

### 2.5 Code

Every module declares which spec identifiers it implements. Without this backlink the
bottom layer floats free of the structure and the whole scheme is decorative.

---

## 3. Entries and identifiers

Every entry at every layer carries:

- **Identifier** — layer prefix plus a flat number. `B·14`, `A·07`, `S·31`.
- **Derives-from** — *vertical*, exactly one layer up. What this entry serves.
- **Depends-on** — *horizontal*, within its own layer. What this entry needs, and the
  only edge that imposes an order.
- **Emits-into** — *horizontal*, and only into a cross-cutting slice. What this entry
  publishes. Fire-and-forget: nothing is consumed back, so it imposes no order.
- **Body** — the content, in that layer's idiom.

The three edge lists answer different questions, and keeping them apart is what makes
slice dependency computable instead of guesswork.

Rules:

- Identifiers are **never reused and never renumbered**. Renumbering silently rots
  every historical reference.
- Identifiers encode **layer and creation order only, never slice**. Slice membership
  is a property of the manifest, not of the identifier. This is what makes §4.6's split
  rule work without renumbering: a split redistributes membership, and every entry keeps
  the identifier it was born with.
- Amendments are **append-only**, with a superseding marker. History is what makes the
  process auditable.
- A derives-from edge may **not skip a layer**. Deriving straight from intent claims a
  derivation nobody wrote down: the layer jumped over cannot be reviewed, and cannot be
  re-derived when the intent changes.
- Identifier plus the edge lists turn a pile of records into a directed graph. That
  graph is the whole system.

---

## 4. Slices

### 4.1 What a slice is

A vertical cut through the system by capability — listings, discovery, messaging. A
slice owns its behaviour, architecture and spec entries. Working on one slice means
loading one complete vertical column.

### 4.2 When slices are defined

Exactly once: **after the behaviour layer is complete, before any architecture is
written.** Not earlier, because slicing on intent prose produces the wrong cuts. Not
later, because architecture entries need somewhere to live.

### 4.3 Who decides

The agent proposes, the human ratifies. The human is answering a question the agent
cannot: does this match how the business actually thinks about the product? Slices that
match the org's mental model survive. Clever technical slices get abandoned.

### 4.4 How the agent proposes

1. **Group by shared nouns.** Behaviours that read and write the same domain object
   almost always belong together. Produces candidate baskets.
2. **Coupling test.** If a typical change touches basket A, does it also touch basket
   B? Constant mutual reference means they are one slice pretending to be two.
3. **Direction test.** If the dependency runs one way only, they are two slices with a
   one-directional dependency — which is *projected from the entries' own edges*, never
   authored beside them. Two statements of the same fact drift, and the one a human
   maintains is the one that goes stale.
4. **Ubiquity test.** If everything touches it and it owns almost no behaviour of its
   own, it is not a slice. It is cross-cutting — see §4.5 for the deciding test.
5. **Size test.** A basket with forty behaviours and no internal structure is probably
   two slices.

These run pairwise across all candidate baskets before anything is proposed.

Do **not** slice by technical layer — frontend, backend, database. That is the classic
failure. Slice where changes land together.

### 4.5 Cross-cutting slices

Cross-cutting is a **slice type, not a shared file and not a reserved name**. Audit
logging deserves its own architecture and its own specs — it is as complex as any slice
and needs its own column to work in. There can be several, each with a full column at
every layer, stored exactly like everything else.

**The deciding test.** Both must pass:

1. **Does the caller branch on what comes back?** If a slice calls it and then changes
   what it does based on the answer, that answer is part of the caller's behaviour, and
   this is an ordinary dependency.
2. **Does its contract name a domain object someone else owns?** If it takes a user, an
   invoice, a listing, it is a dependency, not cross-cutting.

Tiebreak: would a slice added next year invoke this by default, without anyone deciding?

The surviving category is narrow — roughly observability and ambient policy. Narrow is
the correct outcome. Notifications fails test 2 (it takes a recipient, and accounts owns
users). A permission check fails test 1 (it returns allow/deny and the caller branches).
Both are slices.

**Fan-in is not the criterion.** Many slices depending on one slice is a foundational
slice, not a cross-cutting one — `listings` is depended on by everything and is
unambiguously a slice. Fundamentality is topological; cross-cutting is semantic.

**What the type actually changes** is whose context the slice lands in. A cross-cutting
slice's spec spine is included in *every* other slice's work package, whether or not
anything depends on it. That is the whole operational difference: its behaviour ranges
over the other slices rather than naming a subject of its own, so requiring n identical
declarations would fill the dependency graph with edges that are always true and carry
no signal.

Read-only-from-another-slice is **not** a cross-cutting rule — it is universal. One
entry belongs to exactly one slice, and slices define write ownership, so no slice may
ever write into another.

**The edges that are legal.** An edge *into* a cross-cutting slice is an `emits_into`,
never a `depends_on` — depending on something means branching on what it returns, and a
concern whose answer you branch on fails the first test. The two claims cannot both
hold, so the unit refuses the edge rather than leaving it to convention. And a
cross-cutting slice holds **no outbound dependency into a feature slice**: if it has to
ask, it needs to know its caller.

Those two rules together are what makes the classification enforceable rather than
declarative, and they resolve the case that looks like a fourth kind of connection —
two slices leaning on a concern while the concern reaches back at them. That is not a
new shape, it is a cycle. Flip the concern's outbound dependency into an inbound
emission and it has no outbound edges left, so the cycle cannot exist.

### 4.6 Splitting rule

Slices can be **split** later. They can **never be merged** — merging destroys
identifier locality.

---

## 5. Storage and retrieval

Plain filesystem is correct, but only because there is an index on top of it. The tree
is storage; it is not retrieval.

```
manifest.json                slices, identifier membership sets
intent.jsonl
behaviour/
  listings.jsonl
  discovery.jsonl
  messaging.jsonl
  audit.jsonl                a cross-cutting slice is filed like any other
architecture/
  listings.jsonl  …
spec/
  listings.jsonl  …
history/
  amendments-*.jsonl         never loaded by default
```

**One file per slice per layer.** Not one file per entry — per-file overhead in a read
tool kills you at fifty reads. Not one file per layer — large systems drown the context.

**Entries are JSON Lines, not prose.** An entry is a record with a fixed set of
structural fields, and the edges are the load-bearing part. A prose format makes every
new structural field a new regex and a new way to be silently misparsed.

**Spines are the load-bearing idea.** Per layer: identifier, one-line title and edges,
roughly fifteen tokens per entry. The agent loads spines unconditionally, then pulls
full entry bodies **by identifier, on demand**, once it knows from the spine which ones
it needs.

Spines are **computed from the entries on every read, never stored**. A stored spine is
a second copy of the edges that can drift from the first, and drift in the index is the
one thing this whole design cannot tolerate. Nothing outside this unit reads the tree
anyway — retrieval arrives over the API.

Typical session load: manifest, all spines, target column's full entries, the spines of
the columns it depends on, and every cross-cutting spine. Full bodies only for the blast
radius.

**No vector search, no embeddings.** Semantic retrieval is non-deterministic: same
question, different chunks, different day. The edge graph is already a precise
index, and traversing it is deterministic, cheap and explainable. "I loaded `A·14`
because `B·22` derives from it" is auditable. "The retriever ranked it 0.83" is not.

**History files are never loaded by default.** They exist for reconciliation and audit.
If history lives alongside live entries, every read pays for every past mistake.

---

## 6. Gates

A gate is not "human approves layer" — that produces rubber-stamping within days. A
gate is a decision on a specific question about a specific node.

**Admission gates** — mechanical, run by the agent, human sees only failures. They
split along one axis that matters: **sound** blocks, **complete** only reports.

*Sound — a graph in this state is broken now, so amendments are refused and no work
package is issued:*
- Does every entry below trace to something exactly one layer above? (orphans)
- Does every same-layer edge point at a live entry in the same layer? (bad dependencies)
- Is every edge the right *kind* for what it points at — emissions into cross-cutting
  slices, dependencies into everything else? (bad emissions)
- Is the projected slice dependency graph acyclic? (bad slicing — see below)
- Does any cross-cutting slice depend on a feature slice? (misclassification)
- Does any entry belong to more than one slice? (overlapping ownership)

*Complete — the report of what is left to do, never a blocker:*
- Does every entry above have at least one entry below serving it? (unserved
  requirements)

Blocking on completeness too would make every legitimate propagation illegal, because a
half-propagated layer is always incomplete. Blocking on neither makes the gate
decorative.

**A slice cycle is not a scheduling problem.** The entry graph can never cycle —
`derives_from` runs strictly one layer up and `depends_on` strictly within a layer — so
a cycle at slice level is always about how entries were *grouped*. The cut is wrong.
The remedy is to pull the shared part out into a slice they both depend on; merging is
not available, because slices split and never merge. Caught while the slicing is still
a proposal, that remedy is free.

**Judgement gates** — the agent must flag, *as it works*, every call it could not
derive: a tradeoff, an ambiguity in intent, a decision with more than one defensible
answer. The human then reviews a short list of things the agent chose but could not
prove. Five minutes instead of an hour.

The agent declaring its own uncertainty as a first-class artifact is what makes gates
real. If it only ever reports confidence, the gates are theatre.

**Conflict gates** — hard stop. The agent may not proceed.

---

## 6a. Waves

The order slices may be worked in, **computed, never chosen**. A slice's wave is the
*longest* path from it to a slice that depends on nothing.

Longest, not shortest, and that is the whole trick. It guarantees a slice is scheduled
strictly after everything it needs, however long the deepest chain beneath it happens to
be. Shortest-path would put a slice in the same wave as something it depends on the
moment a second, longer route existed.

Two properties fall out, and they are why this is worth computing rather than ordering
by hand:

- **Two slices in the same wave have no edge between them** — structurally, not usually.
  If A depends on B, A's longest path is at least one longer, so they cannot land
  together. Agents working one wave never need to talk to each other, and there is
  nothing to lock.
- **Every earlier wave is complete before the next begins**, so a wave-N agent reads its
  dependencies as frozen artifacts. No coordination, no consistency problem.

A cross-cutting slice lands in **wave 0 by construction** — the edge rules leave it no
outbound dependency to have, so it has no path to anything. Nothing arranges this, which
is a useful sign the edge rules are doing real work.

**Diagnostics.** Every wave one slice wide means the graph is a chain and nothing can be
done in parallel — the slices are too coupled. Reported, never acted on.

Cycles are not a scheduling problem. They are an admission failure, refused at the gate.
The scheduler still has to survive one without hanging, so anything caught in a cycle
comes back as unschedulable rather than looping.

---

## 7. Change: adding a feature

A new feature originates at intent. It enters as an **intent amendment** — a new
numbered entry, never an edit to existing text — and propagates downward.

At each layer the agent asks:
1. What new behaviours does this imply?
2. Do any existing behaviours change or conflict?
3. Does the existing architecture accommodate this?

Question three has two possible answers and **the agent must state which one out loud**:
either the architecture accommodates it, here is how; or it does not, here is the
amendment. An agent quietly bending architecture to fit a feature is precisely where
trust dies.

---

## 8. Change: corrections

When a human says "that's wrong," the first job is **not** to fix it. It is to classify
where the error lives:

- Misunderstood what you wanted → intent defect
- Understood you, described the wrong behaviour → behaviour defect
- Right behaviour, bad structural decision → architecture defect
- Right decision, sloppy code → implementation defect

The fix is applied **at that layer** and re-propagated down — never patched at the point
where the symptom was noticed. Fixing a symptom in code while the spec above still says
the wrong thing means your artifacts now lie, and the next session reads the lie and
reintroduces the bug.

---

## 9. Upward reconciliation

Before any downflow, a correction at layer N is checked against layer N−1. This does
**not** rewrite the layer above; it interrogates it. The question is: does the corrected
version still satisfy every entry above that the original claimed to serve?

Three outcomes, and only three:

1. **Satisfies all of them** — a genuine reasoning slip. The agent had good inputs and
   derived badly. Log it and propagate down.
2. **Satisfies them, but reveals something the layer above never stated** — the upper
   layer was incomplete. Not an agent error: a missing requirement. Promote it upward as
   an amendment *before* the downflow runs.
3. **Contradicts a stated entry above** — real conflict. The agent must stop and not
   propagate. Either the human is overriding intent without realising, or the upper
   layer was wrong. Only a human resolves this.

### Diagnostic value

The distribution of outcomes over time tells you where the pipeline is weak. Mostly
outcome two means intent capture is too shallow. Mostly outcome one means architecture
prompting needs work. This is the debug signal for the agent system itself.

---

## 9a. Issues: the internal queue

An agent deriving one slice discovers another slice's entry is wrong or missing
something. **There is no agent-to-agent channel, deliberately.** That agent is finished
and gone; and a live one would mean either blocking or nondeterminism, and either way the
audit property is lost.

Instead it files an **issue against the artifact** — a numbered entry naming the target
entry, the requesting slice, and a one-line claim — then proceeds on its stated
assumption and flags that assumption as a judgement call. A queue against files, not an
inbox between processes. Kept separate from the human-facing inbox: that one is what the
outside world wants, this is what one part of the pipeline needs from another.

**Two kinds, and the difference is what it costs:**

- **Additive** — a new entry is needed. Nothing existing changes meaning, so nothing
  downstream is invalidated and nothing re-runs.
- **Semantic** — an existing entry that others already consumed now means something
  else. Its consumers are invalidated. Expensive, correctly so, and rare if the slicing
  was good.

Which one it is, is a judgement about meaning — the raiser's call. What is computed is
the *consequence*.

### The router routes; repair sessions resolve

Run **after every wave**, not once per layer: a smaller blast radius, and failures caught
while the context that produced them is still narrow.

The router reads **issue headers only** — target, requester, kind, one-line claim, about
thirty tokens each. Never an assumption, never a target's body. It groups by target slice
and dispatches one repair per slice. That is what keeps its cost flat: a hundred issues
across six slices is six repair sessions, and the expensive reading happens inside a
session that was going to load that column anyway.

### Re-run scope

Computed at the **entry** level, never the slice level. If `B·31` changed meaning, only
the entries declaring `depends_on: B·31` are invalid — usually a handful, not a column.
Direct dependents only: an entry two hops away consumed its *neighbour's* meaning, and
whether that moved is not known until the neighbour is actually repaired.

### Termination

Repairs can raise their own issues. Two rules keep it bounded:

- Repairs run in dependency order, tiebroken by slice name. Arbitrary but deterministic,
  which is what reproducibility needs.
- **Cap at two rounds.** Past that it goes to a human. Without a cap the system
  oscillates and nobody notices until it has burned a day.

And a **semantic issue reaching backwards into a completed wave is a conflict, not a
cascade.** Everything derived from that entry in the waves since is now suspect, and that
signal means the slicing itself was wrong — not something to repair automatically. An
*additive* issue reaching backwards is fine: it invalidates nothing, so nothing behind it
moves.

---

## 10. Spec to code

### 10.1 First iteration

Spec entries are planned into tasks and executed. Each module records the spec
identifiers it implements — the bottom layer's backlink, and the only thing tying a file
to the reasoning that produced it. A module may only claim *spec* entries: one claiming
an architecture entry has skipped the layer that says how, and would be pointing at a
decision rather than an instruction.

### 10.2 Subsequent changes

The planner's input is a **spec-level diff** — which spec entries were added, modified
or superseded. This falls out of propagation for free, and literally so: identifiers are
allocated in creation order from a per-layer counter that only ever moves up, so "created
since state N" is just "numbered above N". No history file, no timestamps, and no second
copy of anything that could drift from the first.

**Do not feed a git diff to the planner as input.** That makes code the source of
truth: the planner starts reasoning about what the code does rather than what the spec
says it should do, and within a few cycles the spec layer is decorative.

The planner resolves the spec diff into two sets:

- **Write set** — modules declaring they implement a changed spec entry. Editable.
- **Read set** — modules implementing entries that *depend on* a changed one but did not
  themselves change. Read-only context. Computed from entry-level edges, so it is the
  modules that actually consumed the changed meaning, not everything in the slice.
- **Unimplemented** — added entries no module claims yet. Not a failure, it is the new
  work — but it has to be visible, or a planner silently emits no task for a requirement
  that has no file yet.

This makes "peeking at related features" a declared, bounded operation instead of the
executor wandering the repo.

### 10.3 Shape of the work unit

- **Deep isolated feature** — one task, full column context, executor goes deep.
- **Wide shallow change** — group by slice, one task per slice, and the task carries the
  *rule* being applied rather than per-module instructions. If the planner emits forty
  near-identical tickets, it has misclassified the change.

### 10.4 Where git diff does belong

Afterwards, as audit. Compare the actual diff against the declared write set. Any file
touched outside that set is a gate failure — either the planner missed a dependency or
the executor freelanced. Both are worth knowing. Mechanical check.

---

## 11. Known open items

Not yet designed. These need closing before implementation.

- **Entry field shape** per layer — the actual fields, not the prose.
- **Session boundaries** — which layers are written by the same agent session and which
  demand a fresh one. Context bleed between layers is real and undermines the separation.
- **Judgement-call criteria** — what the agent is *obliged* to flag, stated concretely
  enough to be enforceable rather than aspirational.
- **Thin intent handling** — when the human says "build me a thing," does the agent
  interrogate or assume-and-flag?
- ~~**Interface-change detection**~~ — **closed.** It looked isolated at spec level only
  while consumption was invisible. It is an edge now: a consumer declares `depends_on`,
  so superseding an entry leaves every consumer pointing at something retired, the
  bad-dependency gate reports each one, and the graph stays unsound — no work package,
  no plan — until they are re-derived.

  The residual case is a slice that consumes another's interface *without* declaring the
  edge. No graph check can see that; the edge is the only evidence there is. It surfaces
  afterwards instead, in the audit, as a file touched outside the write set — which is
  precisely "the planner missed a dependency."

---

## 12. The principles, compressed

1. Every artifact declares what it derives from.
2. Changes enter at their layer of origin and propagate downward.
3. Corrections are classified before they are fixed.
4. Reconciliation runs upward before propagation runs downward.
5. Amendments are append-only; identifiers are permanent.
6. Slices are vertical and persist unchanged to code. Slice-level dependency is
   projected from the entries' own edges, never authored. Cross-cutting is a slice
   type defined by legal edges and by landing in everyone's context — not by fan-in.
7. Retrieval is graph traversal, not similarity search.
8. The agent must declare what it could not derive.
9. Spec is the source of truth for planning; code is the source of truth for audit.
