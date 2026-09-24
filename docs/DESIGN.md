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
slice's spec spine is included in *every* other slice's assembled context, whether or not
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

**Derivation order is not build order.** An emission imposes no ordering on
*derivation* — which is what lets a cross-cutting slice be derived before everything
that emits into it, and what makes a cycle involving one impossible to express. At
*build* time the relation runs the other way: the emitter's code calls the concern's
code, so that symbol has to exist. The two orders differ, and the difference is
invisible only while every cross-cutting work unit sits at wave 0 — which is not
guaranteed, because a cross-cutting slice may depend on another cross-cutting slice, and
`depends_on` within one is unconstrained. Order is computed from `depends_on` alone; the
emission case is checked and reported, never gated.

### 4.6 Splitting rule

Slices can be **split** later. They can **never be merged** — merging destroys
identifier locality.

A split moves membership and nothing else: no identifier changes, so every
historical reference still resolves. It is gated before it is written, because
two halves that referred to each other inside one slice become two slices
referring to each other, and that can be a cycle that did not exist a moment
earlier. Discovering it afterwards would leave the manifest broken with only a
merge to fix it.

Splitting and ratification are the **only** two ways a slice comes into
existence. An amendment naming an unknown slice is refused — a slice is
expensive to get wrong and impossible to undo, so it is never created as a side
effect of writing an entry.

---

## 4a. Work units

A slice is a column to read. A **work unit** is a piece of work to do: a maximal
connected subgraph of the entry-to-module graph — spec entries and the files
implementing them, joined transitively by implements-edges, with nothing outside
connecting in. One unit is one branch.

### The grain was settled by measurement

Three other grains were available, and all three were eliminated by counting rather
than by argument.

- **An entry is too fine.** Two entries living in one file would both write it, and
  `t-finance`'s `bot.py` implements eight.
- **A module is too fine the other way.** An entry spans files: `dark`'s `S·73` spans
  `.env`, `pyproject.toml`, a justfile, a compose file and a preconditions module, and
  eleven of dark's sixty-eight entries span more than one.
- **A slice is too coarse.** It over-serialises, and a module straddling two slices
  belongs to neither exclusively.

Maximal-connected is not a fourth preference. It is the *smallest* grouping whose write
set is disjoint from every other unit's, and that disjointness is the payoff: two
branches in the same wave cannot produce a git merge conflict, because no file is in
both. Forced, not chosen.

**Disjointness is not independence.** A unit's entries may still `depends_on` another
unit's, and then it waits. Two things can be worked at once only with both — disjoint
files and no edge between them.

### Ordering

Projected from spec-entry `depends_on` pushed through the module map, never authored,
for the same reason slice dependency is never authored.

What is handed out is **edges** — the units each unit waits on. Waves (§6a) are a
reporting view over those edges, not the schedule. A consumer that waits for a whole
wave rather than for its own blockers waits for work it does not need: on `dark`, strict
wave barriers would make the last unit wait for fifty-one others when it actually waits
for two.

### Cycles are contracted

Grouping entries into files can create a cycle the entry graph does not have.
`depends_on` is acyclic at entry level because the gates require it, but two files can
each implement one end of the other's dependency. Merging such a group into one unit is
correct rather than a fudge: files that depend on each other cannot be built separately,
so they are one piece of work. A contracted unit is a diagnostic about the module map —
surfaced, never gated.

### Identity, and the cut

Identity **is** the entry set. Same entries, same unit; nothing is allocated and nothing
is stored, so two projections of the same graph agree without consulting each other.

A projection is computed. A **cut** is a projection a person decided to take, and the
difference is why one of them is stored at all: the gates going green is not the same
event as the author being finished. A corpus can be sound and still be mid-revision, and
a projection that recomputed itself silently would move work units under someone who was
still writing — or after work had already been handed out from them. So nothing
recomputes silently. The log is append-only, one line per cut, carrying bodies only for
the units that changed. Re-cutting is free and expected.

There is one hard gate: **no modules, no cut.** A cut with no write sets is a list of
entries nobody can be handed.

---

## 4b. Tests

### 4b.1 What a test entry is

A **test entry is one test scenario**, and it derives from exactly one **spec** entry.
One contract, several ways it can fail, so one spec entry usually has several.

Tests fork off the spec layer rather than continuing the chain toward code. An
implementation module implements spec entries; a test module implements test entries.
Both are code, and code is never an entry — the module backlink is the same one
everything else uses.

```
              ┌──> implementation modules
  S ──────────┤
              └──> T ──> test modules
```

A test entry derives from a spec entry and nothing else. That is not a restriction
working around a limitation; it is the only anchor in this system guaranteed to
survive. Identifiers are never reused and never renumbered, while a module path is
renamed the first time someone tidies a directory — and a test pointing at a renamed
file points at nothing.

The record carries an identifier, the entry it derives from, a free-text **purpose**
read by whoever writes the test, and a body saying what must be observed. Amendments
are append-only with a superseding marker, as everywhere else, and here the reason is
sharper than elsewhere: **a test that can be edited in place is not absolute.**

**`purpose` is free text on purpose.** The test for a closed vocabulary in this design
is whether anything mechanical branches on it. Nothing does — ordering follows from
*what a test derives from*, never from what kind it is claimed to be. Encoding the
distinction twice would be the drift failure this design refuses everywhere else, and
a closed list would refuse the test nobody anticipated.

### 4b.1a Three rules the contract above rests on

Both surfaced while building it. Neither is a new idea; each is what makes a property
already claimed here actually true rather than aspirational.

**A module implements spec entries or test entries, never both.** §4b.2 says the
separation between writing a test and writing the code it judges needs no enforcement,
because the two share no entry and so land in different work units. That holds only
while no single file claims both. One mixed file merges those units, the write sets stop
being disjoint, and a structural guarantee quietly degrades into an honour-system rule.
Refused at declaration. There is deliberately **no `kind` field** on a module: what a
module is follows from the identifiers it claims, and a field would be the second
statement of exactly that.

**A test entry carries no `depends_on` and no `emits_into`.** One scenario needs nothing
from another scenario. This is not tidiness — it is what makes a unit holding test
modules a *root*. With no outbound edge to have, such a unit cannot sit inside a cycle,
so the ordering rule in §4b.3 can never deadlock against a dependency. The same shape as
a cross-cutting slice landing in wave 0: arranged by the edge rules, not by a scheduler.

**Scenarios can be asked for on their own.** Every request type stopped at spec, so a
fresh "say how `S·01` can fail" had nowhere to originate and was refused. A
`verification` request originates at `T`, which is not an exception to the rule that a
change may not enter below its cause: that rule guards against fixing something low
while the layers above go on saying the old thing, and a scenario makes no claim those
layers could contradict. Writing scenarios as part of the `feature` that created the
spec already worked — once a request has produced entries, propagation is
unrestricted — so what this adds is only the standalone ask, for a contract specified
before anyone said how it could fail.

A fourth follows from the rules above rather than being decided: **tests are stored flat
and join no slice** — §5 has the layout and the reason.

### 4b.2 Tests are the second source of truth

Documentation first, tests second, code last. The agent implementing a spec entry
**may read the test files and may never write them.** That is not a rule anyone has to
follow: a test module implements test entries, an implementation module implements
spec entries, the two share no entry, so they fall into different work units — and a
work unit's write set is disjoint from every other's by construction (§4a). Different
unit, different branch, different agent. The separation is a property of the grouping.

### 4b.3 Order

Stated at the level of units, not edges:

> The work unit implementing spec entry `S·X` **follows** any work unit containing test
> modules for test entries derived from `S·X`.

Deliberately not read off `derives_from`. That edge means justification and only
justification, and keeping it apart from `depends_on` is what makes slice and unit
dependency computable rather than guesswork (§3). A third source of unit edges costs
one sentence; redefining an edge would cost the property.

The pairing is **not** one-to-one and must not be assumed to be. A test module may
implement test entries derived from several spec entries — a shared fixture is the
ordinary case — and then several implementation units follow that one test unit. That
fans out; it merges nothing. Anchoring at the entry rather than at the module is what
buys that: a broad test file cannot glue unrelated implementation units together.

### 4b.4 What this makes computable

**Which tests to run after building a unit**: those whose test entries derive from that
unit's spec entries *and* have a module declared. Not a convention — a graph query. It
is the difference between a red suite that gets ignored and a signal, because a test
specified but not yet implemented is not yet expected to pass.

**Which spec entries have no verification**: the same shape as `unserved` and
`unimplemented`, and it is what makes testing automatic rather than something a person
has to remember to ask for at intent.

### 4b.5 What is deliberately out of scope

**Test entries are dev-time tests.** Unit tests, and contract tests between modules —
which anchor to the *provider's* spec entry, consistent with the provider owning the
interface (§4.5).

Acceptance, performance and system-level tests are **excluded, not unrepresentable**.
Three reasons, and the third is the one that matters:

- They are project-specific. Performance criteria are not universally expressible, and
  standardising them would force every project to pretend they are.
- Nothing would branch on the distinction, which is the same argument that makes
  `purpose` free text.
- **A failing acceptance test is not a code defect.** Code derives from documentation,
  so if the documentation is sound the code follows it, and if the documentation is
  wrong an acceptance test derived from the same documentation is wrong the same way.
  What it can catch is the thing the spec was *silent* about — which is a gap in the
  documentation, and belongs in the issue queue (§9a) as a correction, not in a build
  as a failure.

They remain real files that real projects need. They simply live outside this graph,
like a README does. One consequence to accept knowingly: `declare_module` refuses an
empty implements list, so such files cannot be declared, which makes them invisible to
the module map and to the git-diff audit — an agent touching one shows up as an
undeclared write.

### 4b.6 Craft belongs in a prompt, not a field

How to write a good test — what an acceptance-flavoured scenario needs loaded, when a
contract test is worth its cost — is judgement, and judgement belongs to whoever calls
this unit. The per-test instruction rides in `purpose`; the general craft belongs in a
prompt tier. Nothing here interprets either.

---

## 5. Storage and retrieval

Plain filesystem is correct, but only because there is an index on top of it. The tree
is storage; it is not retrieval.

```
manifest.json                slices, identifier membership sets
intent.jsonl
tests.jsonl                  scenarios, flat like intent
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

**Intent and tests are the two exceptions**, and for the same reason: neither has a
slice. Intent is short by nature and everyone reads all of it. A test's column is the
column of the spec entry it derives from, so filing it under one would be a second copy
of a fact the graph already holds — and the copy is what goes stale the first time a
slice splits.

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

### 10.3 Shape of a planned task

- **Deep isolated feature** — one task, full column context, executor goes deep.
- **Wide shallow change** — group by slice, one task per slice, and the task carries the
  *rule* being applied rather than per-module instructions. If the planner emits forty
  near-identical tickets, it has misclassified the change.

### 10.4 Where git diff does belong

Afterwards, as audit. Compare the actual diff against the declared write set. Any file
touched outside that set is a gate failure — either the planner missed a dependency or
the executor freelanced. Both are worth knowing. Mechanical check.

---

## 10a. Measuring the pipeline

Everything above says how the pipeline works. This says how you find out whether it is
working — and it is governed by one rule that matters more than any metric in it:

> **Nothing here ever gates.**

Every gate in §6 blocks on something *definitionally* broken: a cycle cannot be
scheduled, a dangling edge points at nothing, an entry with two owners has none.
Everything in this section is a **proxy** for a question nobody can answer yet — *is
this slicing any good* — and baking a proxy into the one place the system is supposed to
be certain would be the worst trade available. These report. They never refuse.

Nor is anything here a *verdict*. A slicing that scores badly may be right and the unit
has no way to know. Callers get inputs to a judgement.

### 10a.1 The primary score is not a proxy

A good slicing is one where **a typical change lands inside one slice**. That is the
definition, not a stand-in for it — and it is directly measurable, because every request
already records exactly which entries it produced.

**Change locality**: for one request, how many distinct slices do its produced entries
fall into? One is perfect. The distribution over many changes is the score.

Intent is excluded. Every change touches it, so counting it would add one to every score
and distinguish nothing.

Everything else in this section is secondary to that number.

### 10a.2 Three tiers, separated by when the data exists

This is the real difficulty, and pretending otherwise would make the weak numbers look
strong.

**Ex ante, at slicing time.** Only intent and behaviour exist — no `depends_on` edges
worth speaking of, no architecture, no history. Two signals, and that is genuinely all:

- **Shared parentage** — entries deriving from the same parent are usually about the
  same thing. The strongest signal available, and free, because the edge is already
  there.
- **Spread** — a parent whose children scatter widely is either constraint-shaped
  ("every action must be auditable", a cross-cutting tell) or evidence the cut runs
  across the grain of intent. Two opposite readings from one number, which is exactly why
  it is reported for an agent to argue from rather than acted on.

This filters an obviously bad proposal. It does not pick the best one, and should not be
described as though it does.

**Structural, once a layer has propagated.** Cohesion (the share of a slice's dependency
edges that stay inside it), coupling, fan-in and fan-out asymmetry, wave shape, work
package ratio. Real numbers, and they arrive incrementally — cross-slice edges appearing
at architecture that nobody predicted at behaviour is an early warning, and it lands
while a split is still cheap. Splits are always available; merges never are.

Emissions are excluded from coupling. They impose no order and cross into a concern by
design, so counting them would penalise exactly what the edge exists to make cheap.

**Retrospective, from history.** Change locality, the correction distribution, issue-pair
density, escalation rate, repair rounds used.

One honesty note: the issue-derived metrics are **endogenous**. That history was produced
under one particular slicing, so it partly measures the cut that generated it. Change
locality is the least contaminated — the entries a feature needs are mostly a property of
the feature, not of how you filed them.

### 10a.3 Scoring a cut that does not exist

Slices split and never merge, so a ratified cut is expensive to undo forever. The
remedy for that is to move the argument earlier: take a *proposed* partition, run every
gate and every structural metric against it as though it were real, and commit nothing.

This is what makes trialling several slicings possible at all, and it is where §6's
decision to hard-block conflicts pays off — a cycle caught in a proposal costs a rename;
the same cycle after ratification costs a split.

It returns a result **even when the cut is illegal**. Refusing would make trialling
impossible, which is the entire point of the mechanism.

And because membership comes from the proposal rather than the manifest, the same change
history can be replayed under an alternative cut. That is the trial harness: same
changes, different slicing, different score.

### 10a.4 The lifecycle log

The graph answers *what is true now*. It cannot answer *how it got that way*, and three
things in particular vanish into a correct-looking graph:

- A gate that failed and was then fixed leaves no trace in the fixed graph.
- A correction's **layer of origin** — the whole point of §8's classification — is
  invisible once the fix has propagated.
- What an agent **could not derive, and assumed instead** is nowhere in the state at all.

So those are recorded as they happen: requests as they were actually worded, derivations,
corrections with the layer they entered at, refusals, classifications, issues with their
assumptions, plans, audits.

Deliberately **not a second copy of the graph.** Events carry identifiers, never bodies.
A log that duplicated content would go stale against the thing it copied, which is the
same argument that keeps dependencies out of the manifest and spines out of storage.

### 10a.5 What this is for

§9 already said it: the distribution of outcomes over time is the debug signal for the
agent system itself. Corrections clustered at intent mean the interview is too shallow to
derive from; clustered lower, the derivation prompting is weak.

That is a **learning signal across projects**, not a fix for the current one. The
retrospective numbers arrive months after the decision they judge. The one genuinely
actionable mid-flight signal is structural drift — predicted coupling at slicing time
against measured coupling at architecture — because that lands while a split is still
cheap.

Steering on any of this, when there is evidence for it, belongs in what a proposal
*reports*, never in what the graph *refuses*.

---

## 10b. One writer, one layer

The rule the rest of the pipeline is arranged around, and the one thing here
that is enforced rather than asked for.

**An amendment writes entries at one layer.** If a single writer produces
behaviour and then the architecture beneath it, it already knows why it wrote
the behaviour, so it never reads it -- and the `derives_from` edge claims a
derivation nobody performed. **Every gate still passes.** The graph is well
formed and it is lying, which is the one failure mode the gates are
structurally unable to see.

That was prose in six prompts for a while, and a repair agent duly wrote two
layers in one amendment. It is a refusal now: a mechanical check does not
need an agent, it needs a function.

The rule is one layer, not one entry. Serving six parents is six entries in
one transaction.

---

## 11. Known open items

Design, not code. Every mechanical operation this document calls for is implemented;
what remains open is judgement about how the agent should behave, and none of it blocks
anything that exists.

- **Entry field shape** per layer — the actual fields, not the prose. The graph needs
  only an identifier, the edge lists and a body; these constrain what goes *inside* a
  body.
- ~~**Session boundaries**~~ — **closed.** One session writes one layer, and below
  intent one slice of it. Context bleed is not a tolerance to be managed but a
  correctness problem: a session that writes two layers produces a `derives_from` edge
  claiming a derivation it never performed, and every gate still passes. §10b has the
  argument.
- **Judgement-call criteria** — what the agent is *obliged* to flag, stated concretely
  enough to be enforceable rather than aspirational.
- **Where the test guidebook ships.** §4b.6 settles that craft belongs in a prompt
  rather than a field, not which prompt. A new tier, or additions to the existing ones,
  is a question about the prompts as they now stand rather than about the design.
- **Files several work units must edit** — `pyproject.toml`, `.gitignore`, a shared
  `__init__.py`. Declared, they glue every unit that touches them into one and
  parallelism dies; undeclared, the disjointness guarantee is void the moment two
  branches both append a line. **Accepted as unsolved rather than open**: it is not
  answerable at this level of abstraction, merge conflicts on such files will happen,
  and a mechanism pretending otherwise would be worse than the honest gap. Recorded so
  nobody reopens it expecting an answer to be waiting.
- **A spec diff does not see a test.** §10's planner resolves a *spec* diff into a write
  set, so amending a scenario produces an empty plan. Not a defect in the planner: the
  execution scope is a work unit, and `get_work_unit` carries both the scenarios to run
  and the ones not yet built. Recorded because the two surfaces now disagree about what
  counts as a change, and whichever of them survives should be the one that says so.
- **Thin intent handling** — when the human says "build me a thing," does the agent
  interrogate or assume-and-flag? `prompts/reference.md` holds the settled half — what
  an `initiate` request must *produce* — and is explicit that the technique is not
  settled.
- **Whether the ex-ante metrics are worth anything.** §10a.2 is honest that shared
  parentage and spread are weak. Whether they correlate with change locality is an
  empirical question that needs projects to have run, and until then they are reported
  and believed cautiously.
- ~~**A holding place for behaviour written before slicing**~~ — **closed.** It was
  listed as unbuilt and turned out to be load-bearing: storage demanded a slice name
  for layer B, while slices are cut *after* behaviour is complete, so the designed
  order could not be executed at all. Behaviour with no slice goes to a holding file
  and belongs to no slice, which is precisely what makes it visible to the slicing
  rung.

- ~~**Interface-change detection**~~ — **closed.** It looked isolated at spec level only
  while consumption was invisible. It is an edge now: a consumer declares `depends_on`,
  so superseding an entry leaves every consumer pointing at something retired, the
  bad-dependency gate reports each one, and the graph stays unsound — nothing is issued
  to build from, no plan — until they are re-derived.

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
10. Measurement reports and never refuses. Gates block on what is definitionally broken;
    a metric is a proxy for a question nobody can answer yet, and a proxy must never be
    given the authority of a certainty.
