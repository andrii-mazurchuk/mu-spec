# Layered specification pipeline — consolidated decisions

Authoritative as of this revision. Supersedes `final.md`, which contains two errors
listed in §11.

Audience: implementation agents and humans working on this system. Every statement here
is a decision, not a suggestion. Where something is undecided it says so explicitly in
§10.

---

## 1. What this system is for

Agents produce code whose justification is invisible. You cannot tell a correct
implementation of a wrong decision from an incorrect implementation of a right one.

This pipeline makes the reasoning auditable by requiring every artifact to declare what
it derives from. The payoff is a **mechanically computable blast radius**: any change
has a determinable set of affected entries, so human review is scoped to that set
rather than to whole documents.

---

## 2. Layers

Five. Ordered.

| Layer | Answers | Sliced |
|---|---|---|
| Intent | Why — the buyer's problem and constraints | No |
| Behaviour | What — observable, testable, implementation-free | Filed after gate |
| Architecture | How — flows, boundaries, data movement | Yes |
| Implementation spec | With what — libraries, modules, interfaces, layout | Yes |
| Code | The artifact | Yes |

**Intent.** Where requirements end from the human side. One file, short, everyone reads it.

**Behaviour.** The layer most pipelines omit, and the reason they fail. Intent is too
vague to verify against; architecture is decisions, and decisions can be defensible and
wrong. Behaviour is the only testable artifact that is not code. Stated as: given this
input the system does this; this actor can do this and cannot do that; when this fails
the user sees this. Authored as a flat list, then filed into slices at the slicing gate.

**Architecture.** Flows, boundaries, standards binding all later code. No library
choices, no implementation detail.

**Implementation spec.** The gap between "data flows this way" and "write this
function." Named libraries, module boundaries, interfaces, file layout. Kept separate
from architecture because it churns fastest and churn must not drag architecture with it.

**Code.** Every module declares which spec identifiers it implements. Without this
backlink the bottom layer floats free and the scheme is decorative.

---

## 3. Entries and identifiers

An entry is JSON-shaped with at minimum:

- `id` — layer prefix plus number
- `derives_from` — upstream ids, one layer up
- `depends_on` — same-layer entry ids
- `body` — content in that layer's idiom

**Identifier rules — non-negotiable:**

- Allocated **globally per layer** from a single counter, in creation order. `B·43` is
  the 43rd behaviour entry authored in the project.
- An identifier encodes **layer and creation order only**. It **never encodes slice.**
- Never reused. Never renumbered. Renumbering silently rots every reference in the system.
- Amendments are append-only with a superseding marker.

The reason identity must not encode location: if it did, moving an entry would change
its identity, and slice splitting would be impossible.

**Slice membership is a separate, mutable mapping** held in the manifest — an explicit
membership set per slice, never a range. Slices interleave in the number space over
time. That is correct, not untidy.

---

## 4. Slices

### 4.1 Definition and timing

A vertical cut through the system by capability. A slice owns its behaviour,
architecture and spec entries and persists unchanged down to a code folder.

Defined **once**: after the behaviour layer is complete, before any architecture is
written. Not earlier — slicing on intent prose produces wrong cuts. Not later —
architecture entries need somewhere to live.

### 4.2 Authority

Agent proposes, human ratifies. The human answers a question the agent cannot: does this
match how the business actually thinks about the product? Slices matching the org's
mental model survive; clever technical slices get abandoned.

### 4.3 Proposal procedure

1. **Group by shared nouns.** Behaviours reading and writing the same domain object
   belong together. Produces candidate baskets.
2. **Coupling test.** Does a typical change touch both baskets? Constant mutual
   reference means one slice pretending to be two.
3. **Direction test.** One-way dependency means two slices plus a declared edge.
4. **Ubiquity + ownership tests** (§5) — is this cross-cutting rather than a slice?
5. **Size test.** Forty behaviours with no internal structure is probably two slices.

Run pairwise across all candidate baskets before anything is proposed.

**Never slice by technical layer** — frontend, backend, database. Slice where changes
land together.

### 4.4 Split rule

Slices may be **split**. Slices are **never merged** — merging destroys identifier
locality. A split changes membership sets only; no identifier moves, no edge breaks.
Recorded as a manifest amendment.

---

## 5. Cross-cutting slices

### 5.1 They are a slice *type*, not a file

Superseded decision: cross-cutting concerns are **not** one shared file per layer. Audit
logging deserves its own architecture entries and its own specs — it is as complex as
any slice and needs its own cognitive column for the agent working it.

A cross-cutting slice gets a full column at every layer: own behaviour entries, own
architecture, own specs, own code folder. What distinguishes it is **which edges are
legal**, not how it is stored.

### 5.2 Classification test

A concern must pass **both** to be cross-cutting.

**Test 1 — does the caller branch on what comes back?**
If slice A calls C and then changes what it does based on C's answer, C is part of A's
behaviour and is an ordinary dependency.

**Test 2 — does C's contract name a domain object owned by someone else?**
If the contract mentions users, invoices, listings — objects another slice owns — it is
a dependency, not cross-cutting.

**Tiebreak — would a brand-new slice added next year invoke this by default, without
anyone deciding?**

### 5.3 Worked classifications

| Concern | Test 1 | Test 2 | Verdict |
|---|---|---|---|
| Audit logging | Passes — returns nothing to branch on | Passes — actor, action string, opaque target id | Cross-cutting |
| Error reporting | Passes | Passes — error plus context blob | Cross-cutting |
| Telemetry | Passes | Passes — event name plus properties | Cross-cutting |
| Notifications | Passes — fire and forget | **Fails** — takes a recipient user; accounts owns users | Slice |
| Billing | **Fails** — caller branches on payment cleared | — | Slice |
| Accounts | **Fails** — caller branches on identity | — | Slice |
| Permission check | **Fails** — returns allow/deny, caller branches | — | Slice |

Note the tests discriminate independently — notifications and billing fail on different
tests. That is what makes the procedure decidable rather than a vibe.

**Fundamentality is not the criterion.** Many slices depending on one slice is just a
foundational slice. Accounts sits under nearly everything and is unambiguously a slice.
Fan-in count never enters the judgement.

**The surviving category is narrow** — roughly observability and ambient policy. Narrow
is the correct outcome. Internationalisation passes both tests but is a genuinely
debatable case; treat it as open.

### 5.4 Edge rules

- A cross-cutting slice has **zero outbound `depends_on` edges into feature slices.**
  Structural, not advisory.
- It may depend only on other cross-cutting slices.
- Edges into it are `emits_into`: non-blocking, no consumed return value. The emitting
  slice publishes a payload shape; the concern consumes whatever arrives and never names
  the emitter back.
- **Detector for misclassification:** if a cross-cutting slice needs to *ask* a feature
  slice for anything, it is not cross-cutting.

---

## 6. The edge taxonomy

**Two node types:** slices, cross-cutting slices.

**Three legal edge types:**

| Edge | Direction | Notes |
|---|---|---|
| `derives_from` | Vertical, one layer up | Every entry has at least one |
| `depends_on` | Horizontal, same layer | Must form a DAG |
| `emits_into` | Slice → cross-cutting | Never traversed in reverse |

**One illegal:** any cycle in `depends_on`.

**Slice-level dependency is derived, never authored.** Slice A depends on slice B iff
some entry in A depends on some entry in B. If both existed as authored facts they would
drift and the manifest would lie. Deriving it also gives cycle detection for free.

### 6.1 The four connection cases, resolved

1. **One-way A→B.** Normal. Legal.
2. **Bidirectional.** Illegal. Admission gate rejects.
3. **A and B both depend on C, C is "fundamental".** Legal and ordinary — C is a
   foundational slice. Fundamentality is topological; cross-cutting is semantic. Apply
   §5.2 tests, not fan-in count.
4. **A and B depend on C, and C depends on A and B.** Not a fourth type — this is case 2,
   a cycle, and it is disallowed. It is *also diagnostic*: this is exactly what a
   cross-cutting concern looks like when wrongly modelled as a feature slice. Resolution
   is to **invert the C→A edge into an `emits_into`**, after which C has zero outbound
   edges and the cycle cannot exist. If C is genuinely a feature slice, the cycle means
   the slicing is wrong.

---

## 7. Storage and retrieval

Plain filesystem, with an index on top. The tree is storage; it is not retrieval.

```
manifest.json                slices, type field, membership sets, derived dep graph
intent.md
spine/
  behaviour.jsonl            id · one-line title · derives_from · depends_on · slice
  architecture.jsonl
  spec.jsonl
behaviour/
  listings.jsonl
  discovery.jsonl
  audit.jsonl                cross-cutting slices sit here, same as any other
architecture/
  listings.jsonl  …
spec/
  listings.jsonl  …
issues/
  open.jsonl                 §9
history/
  amendments-*.jsonl         never loaded by default
```

**One file per slice per layer.** Not one file per entry — per-file read overhead kills
you at fifty reads. Not one file per layer — large systems drown the context.

**Spines are load-bearing.** One per layer, carrying id, one-line title and edges only.
Roughly fifteen tokens per entry. Loaded unconditionally. Bodies load **by id, on
demand**, once the spine tells the agent which ones it needs.

**Session load:** manifest, all spines, target column's full entries, dependency
columns' spines only, cross-cutting spines. Full bodies only for the blast radius.

**No vector search, no embeddings.** Semantic retrieval is non-deterministic: same
question, different chunks, different day. The edge graph is already a precise index;
traversal is deterministic, cheap and explainable. "I loaded `A·14` because `B·22`
derives from it" is auditable. "The retriever scored it 0.83" is not.

**History never loads by default.** Otherwise every read pays for every past mistake.

---

## 8. Execution: waves

### 8.1 Wave assignment

Computed, never chosen.

1. Build the `depends_on` DAG at entry level.
2. Project to slice level.
3. Each slice's wave = longest path to a root.

Cross-cutting slices land in **wave 0 by construction**, since they have zero outbound
edges. This is a useful confirmation that the edge rule does real work.

Cycles are not a scheduling problem — they are an admission failure. The gate rejects
and you reslice.

### 8.2 Why this is cheap

**Two slices in the same wave have no edge between them, by construction.** Parallel
agents within a wave never need to communicate. Not rarely — structurally never.

Every wave-N agent reads its dependencies as **frozen** artifacts, because those waves
are complete. No locks, no coordination, no consistency problem.

**Write discipline:** an agent writes only its own column. Reads dependency spines
(bodies on demand) and cross-cutting spines. Everything else is invisible to it.

Ordering **within** a wave does not exist — parallel, no edges.

### 8.3 Diagnostics

- Waves one slice wide → the DAG is a chain, slices are too coupled. Reslice.
- Semantic issues common rather than rare → a dependency is mismodelled; something
  upstream does not own what it claims to.

---

## 9. Issues and reconciliation

### 9.1 No agent-to-agent messaging

Rejected deliberately. The upstream agent is gone; even if live, message-passing means
either blocking or nondeterminism, and the audit property is lost.

Instead an agent emits a **request against the artifact**: a numbered issue entry naming
target slice, target entry id, requesting slice, and a one-line claim. A queue against
files, not an inbox between processes. The agent then proceeds on its stated assumption
and flags it as a judgement call.

### 9.2 Reconciliation timing

**After every wave.** Not once per layer. Smaller blast radius, failures caught while
context is still narrow.

### 9.3 The reconciler is a router, not a designer

This is what keeps its context flat regardless of issue count.

It reads **issue headers only** — target slice, target entry, requester, one-line claim.
About thirty tokens each. It never opens issue bodies. It never opens slice files. It
does exactly two things: classify each issue additive vs semantic, and group by target.

Then it dispatches **one repair session per target slice**, handling all issues against
that slice together. Those sessions load normally — the expensive reading happens in a
session that was going to load that column anyway. A hundred issues across six slices is
six repair sessions, not one enormous one.

### 9.4 Additive vs semantic

**Additive** — a new entry; no existing entry changes meaning. Apply it. Nothing
downstream is invalidated. No re-runs.

**Semantic** — an existing entry that others already consumed now means something
different. Invalidates consumers. Expensive, correctly so, and rare if slicing was good.

### 9.5 Re-run scope

Computed from the **entry-level** graph, not slice level. If `B·31` changed meaning,
only entries declaring `depends_on: B·31` are invalid. Usually a handful, not a column.

### 9.6 Termination

Repairs can raise their own issues. Two rules keep this bounded:

- Repair sessions within a round run in dependency order. Tiebreak within a round:
  lowest slice id. Arbitrary but deterministic, which is what reproducibility needs.
- **Cap at two rounds.** Anything unresolved becomes a conflict gate and goes to a human.
  Without a cap the system can oscillate and you will not notice until it has burned a day.

### 9.7 Repairs reaching backwards

A wave-2 repair invalidating a **completed earlier wave** is a **conflict gate**, not a
cascade. Cheaper, and it puts a human on the rare genuinely-wrong-slicing case, which is
what that signal actually means.

---

## 10. Change and correction

### 10.1 New feature

Originates at intent. Enters as an intent amendment — a new numbered entry, never an
edit to existing text — and propagates down. At each layer:

1. What new behaviours does this imply?
2. Do any existing behaviours change or conflict?
3. Does the existing architecture accommodate this?

Question three has two answers and **the agent must state which out loud**: it
accommodates, here is how; or it does not, here is the amendment. An agent quietly
bending architecture to fit a feature is where trust dies.

### 10.2 Correction

The first job is **not** to fix it. It is to classify where the error lives:

- Misunderstood what you wanted → intent defect
- Understood you, described the wrong behaviour → behaviour defect
- Right behaviour, bad structural decision → architecture defect
- Right decision, sloppy code → implementation defect

Fix at that layer, re-propagate down. Never patch at the point where the symptom
appeared — that makes the artifacts lie, and the next session reads the lie and
reintroduces the bug.

### 10.3 Upward reconciliation

Before any downflow, a correction at layer N is checked against layer N−1. This does not
rewrite the layer above; it **interrogates** it. Question: does the corrected version
still satisfy every entry above that the original claimed to serve?

1. **Satisfies all** — genuine reasoning slip. Log, propagate down.
2. **Satisfies all, but reveals something never stated above** — the upper layer was
   incomplete. Not an agent error: a missing requirement. Promote upward as an amendment
   **before** downflow.
3. **Contradicts a stated entry above** — real conflict. Stop. Do not propagate. Only a
   human resolves.

**Diagnostic:** the outcome distribution tells you where the pipeline is weak. Mostly
outcome 2 means intent capture is too shallow. Mostly outcome 1 means architecture
prompting needs work.

---

## 11. Gates

A gate is not "human approves layer" — that produces rubber-stamping within days. It is
a decision on a specific question about a specific node.

**Admission gates** — mechanical, agent-run, human sees only failures:

- Every entry below traces to something above (no orphans)
- Every entry above has something below serving it (no unserved requirements)
- No cycles in `depends_on`
- No cross-cutting slice holds an outbound `depends_on` into a feature slice
- No dependency arrow points against a declared direction

**Judgement gates** — the agent flags, *as it works*, every call it could not derive: a
tradeoff, an ambiguity, a decision with more than one defensible answer. The human
reviews a short list of things the agent chose but could not prove. Five minutes, not an
hour.

The agent declaring its own uncertainty as a first-class artifact is what makes gates
real. If it only reports confidence, the gates are theatre.

**Conflict gates** — hard stop. The agent may not proceed.

---

## 12. Spec to code

### 12.1 The planner's input is a spec-level diff

Which spec entries were added, modified, superseded. Falls out of propagation for free.

**Do not feed a git diff to the planner as input.** That makes code the source of truth:
the planner reasons about what the code does rather than what the spec says it should do,
and within a few cycles the spec layer is decorative.

### 12.2 Write set and read set

- **Write set** — modules declaring they implement a changed spec entry. Editable.
- **Read set** — modules depending on those, whose own spec entries did not change.
  Read-only context.

This makes "peeking at related features" a declared, bounded operation instead of the
executor wandering the repo. It requires the module→spec backlink from §2.

### 12.3 Work unit shape

- **Deep isolated feature** — one task, full column context, executor goes deep.
- **Wide shallow change** — group by slice, one task per slice, task carries the *rule*
  being applied rather than per-module instructions. Forty near-identical tickets means
  the planner misclassified the change.

### 12.4 Where git diff belongs

Afterwards, as audit. Compare the actual diff against the declared write set. Any file
touched outside it is a gate failure — the planner missed a dependency, or the executor
freelanced. Both worth knowing. Mechanical check.

---

## 13. Corrections to `final.md`

Two errors in the earlier document, both now fixed above.

1. **"Identifier ranges" in §5.** Wrong word, and it smuggled in the assumption that an
   identifier encodes slice. It must not. Replaced by **membership sets**.
2. **`B·X1–X5` notation for cross-cutting.** Contradicts the rule that identifiers encode
   layer and creation order only. Cross-cutting entries draw from the same per-layer
   counter as everything else. **Kill the X prefix.**

Also superseded: cross-cutting as one shared file per layer. It is a **slice type** with
a full column at every layer (§5.1).

---

## 14. Open — not yet decided

Do not let an implementation agent invent answers to these silently.

- **Entry field schema per layer.** The actual fields beyond the four in §3.
- **Session boundaries.** Which layers share an agent session and which demand a fresh
  one. Context bleed between layers is real and undermines the separation.
- **Judgement-call criteria.** What the agent is *obliged* to flag, concrete enough to be
  enforceable rather than aspirational.
- **Thin intent handling.** When the human says "build me a thing" — does the agent
  interrogate, or assume and flag?
- **Interface-change detection.** A spec change altering an interface other slices consume
  looks isolated at spec level and is not. Highest-risk undetected case in the design.
- **Internationalisation classification.** Passes both cross-cutting tests but its specs
  are entangled with presentation. Genuinely arguable.

---

## 15. Principles, compressed

1. Every artifact declares what it derives from.
2. Identifiers encode layer and creation order only — never slice, never renumbered.
3. Slice membership is a mutable set; slices split, never merge.
4. Cross-cutting is a slice type defined by legal edges, not by fan-in or importance.
5. Slice-level dependency is derived from entry-level edges, never authored.
6. Waves are computed from the DAG; same-wave agents never communicate.
7. Coordination happens through queued issues against artifacts, never agent-to-agent.
8. The reconciler routes; repair sessions resolve.
9. Changes enter at their layer of origin; reconciliation runs up before propagation runs down.
10. Retrieval is graph traversal, not similarity search.
11. The agent must declare what it could not derive.
12. Spec is the source of truth for planning; code is the source of truth for audit.
