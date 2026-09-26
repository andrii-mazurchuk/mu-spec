# Writing into the derivation graph

The mechanical contract. What this unit accepts, what it refuses, and why
each refusal exists. Fetched by anything about to write.

The judgement half -- what belongs at which layer, and how to run an effort
that fills the graph in -- is `GET /prompts/wayfinding`.

---

## The shape of an entry

```json
{"layer": "S",
 "title": "capture/store.py: an append-only store whose every read names an account",
 "derives_from": ["A·04"],
 "depends_on":   ["S·02"],
 "emits_into":   ["S·31"],
 "body": "Module capture/store.py. CapturedRecord is frozen ..."}
```

- **`layer`** is one of `I`, `B`, `A`, `S`, or `T`. You never choose the
  identifier; this unit allocates it. `T` is a **test**: one scenario,
  deriving from exactly one spec entry, carrying no horizontal edge of
  either kind and no slice. It may also carry a free-text `purpose` for
  whoever writes the test; nothing mechanical branches on it.
- **`title`** is one line, and it is what everyone reads in the spine
  forever. Write it as a claim, not a label: `A person can see where one
  month's money went`, never `Reporting`.
- **`body`** carries the substance, and at spec level it carries enough to
  implement and test from.

Three kinds of edge, answering three different questions:

| edge | means | direction |
|---|---|---|
| `derives_from` | what this **serves** | vertical, exactly one layer up |
| `depends_on` | what this **needs** | horizontal, same layer |
| `emits_into` | what this **publishes** | horizontal, into a cross-cutting slice only |

`depends_on` is the only edge that imposes an order, which is why the wave
schedule is computed from it and from nothing else. An emission imposes no
order at all: nothing comes back, so nothing waits.

---

## Writing

**Everything from outside goes to `POST /inbox`.** You never name a layer
there, and there is deliberately no way to. The request's `type` decides how
deep the change it authorises may reach:

| type | may originate at |
|---|---|
| `initiate` | intent, and creates the project |
| `feature` | intent |
| `correction` | intent or behaviour |
| `verification` | test |
| `comment` | nothing |
| `question` | nothing |

Then `POST /projects/{project}/amendments`, citing the request:

```
curl -sS -X POST "$BASE/projects/$P/amendments" \
  -H 'Content-Type: application/json' -d @- <<'JSON'
{"in_response_to": "msg-0001", "slice": "capture", "entries": [ ... ]}
JSON
```

**Send the body on stdin, not as one long argument.** A command line of a
few kilobytes gets truncated by the shell, and a truncated amendment is a
malformed one.

**To depend on an entry in the same amendment, cite it by position.**
`"depends_on": ["#0"]` means the first entry in this batch. Identifiers are
allocated on commit, so before this you had two options and both were wrong:
guess a number, or leave the edge out and put the ordering in prose. A guess
naming something that does not exist is caught — but a guess that lands on a
real identifier belonging to a *different* entry is a well-formed edge saying
something false, and there is no gate that can see it.

Placeholders work in `depends_on` and `emits_into`. Not in `derives_from`:
that points one layer up, and an amendment writes one layer, so an entry in
this batch is never a legal parent.

**`slice` must already exist.** A slice comes from ratifying a slicing
proposal, or from `split_slice`. Naming a new one here is refused. Slices
split and never merge, so one created by a typo is permanent.

---

## What will be refused, and why

Each of these is a hard error the caller sees, not a warning in a log.

**An amendment that cites no request.** Every entry traces out past the graph
to the person who wanted it. An amendment nobody asked for has no such trace.

**An amendment carrying two layers.** One writer, one layer. A writer that
produces behaviour and then the architecture beneath it already knows why it
wrote the behaviour, so it never reads it, and the `derives_from` edge claims
a derivation nobody performed. Every gate still passes; the graph is well
formed and lying. Submit the upper layer and let the next pass derive from
what is actually written. The rule is one layer, not one entry -- serving six
parents is six entries in one transaction.

**A `derives_from` that skips a layer.** The layer jumped over can never be
reviewed and can never be re-derived when the intent above it changes.

**An orphan** -- anything below intent that derives from nothing, or from
something retired or out of layer.

**A same-layer edge pointing at nothing**, at itself, or across layers.

**A second owner for an entry.** One entry belongs to exactly one slice.
Two owners means two writers may edit it, both do, neither knows, and the
audit passes for both.

**A slice cycle**, or a cross-cutting slice reaching into a feature slice.

**Architecture or spec with no slice.** Both are derived after slicing, so
an owner always exists. Behaviour is the exception: slices are cut *after*
behaviour is complete, so behaviour with no slice is normal and goes to a
holding place until a partition is ratified.

**An entry deeper than the citing request may originate at.** Fixing
something low while the layers above still say the old thing is exactly how
the artifacts start lying.


---

## Writing a scenario

A **scenario** is one test entry: one way a contract can be caught failing.
It derives from exactly one spec entry and from nothing else, carries a
free-text `purpose`, and says in its body what must be observed.

Ask for them with a `verification` request. One request covers a whole
drawdown — batch as many scenarios as you like into each amendment, and keep
citing the same request.

### One scenario, one failure

Not one per contract, and not one per function. As many as there are distinct
ways the contract can be broken, which is usually several and occasionally
one.

The test is whether the scenarios could fail *independently*. If two would
always fail together, they are one scenario written twice. If one could pass
while the other fails, they are two.

### Write the title as the observation, not the subject

The title is what everyone reads forever, and it should state what must be
true — so a reader knows what broke from the failure line alone.

> ✅ `An empty query returns everything, not nothing`
> ✅ `A second capture on the same day does not advance the streak`
> ❌ `Test the empty query case`
> ❌ `Streak edge cases`

A title naming a *subject* rather than a *claim* tells you which area broke
and nothing more, which is the position you were already in.

### The body says what is observed, never how

Enough to write the test from without reading the code, and no more:

> Advance the streak, then advance it again within the same day. The streak
> is unchanged and no announcement is emitted.

Do not name private functions, module layout, or call order. A scenario that
breaks when the module is reorganised was testing the implementation, and the
implementation is not the contract. The scenario must survive a rewrite that
keeps the behaviour.

### `purpose` is for the human who writes the test

Free text, and nothing mechanical reads it. Use it for the thing a careful
implementer would still get wrong — the reason this scenario is worth its
line:

> `the boundary the spec is silent about`
> `the day rule, which is the entire reason the cache exists`
> `the failure path is the one nobody instruments`

Not a restatement of the title. If `purpose` and the title say the same
thing, delete `purpose`.

### One parent, and choosing it

`derives_from` takes exactly one spec entry. That is not a limitation to work
around — it is what lets a failure name the contract it falsifies, and what
keeps the ordering rule from dragging unrelated work behind a scenario that
never judged it.

When a scenario seems to need two, it is testing an interaction. **Anchor it
to the provider** — the entry that owns the interface — the same way a
contract test does. If that still feels wrong, the interaction itself is
probably undocumented, and that is an issue against the spec, not a scenario.

### A scenario carries no horizontal edges

No `depends_on`, no `emits_into`, and both are refused. One scenario needs
nothing from another. This is also what leaves a work unit holding test files
with no outbound edge, so it cannot sit in a cycle and the ordering that puts
implementation after tests can never deadlock. It does not put such a unit at
the front of the project: it is scheduled immediately before the
implementation it judges.

### What is not a scenario

Acceptance, performance and system-level tests are out of scope, and the
reason matters more than the rule: **code derives from the documentation, so
a test derived from that same documentation is wrong in exactly the way the
documentation is wrong.** What such a test actually catches is something the
spec was *silent* about — and a silence is a gap in the documentation. Raise
an issue against the entry. Do not encode it as a scenario that will fail
forever and teach everyone to ignore a red suite.

Those tests remain real files that real projects need. They simply live
outside this graph, like a README does.

### Then declare the file

```
POST /projects/{project}/modules
{"path": "tests/test_rates.py", "implements": ["T·04", "T·05"]}
```

**A module implements spec entries or test entries, never both.** Refused
otherwise, and the refusal is load-bearing. A unit's write set is every module
implementing *its own* entries, so a file claiming both kinds would put the
test file inside the **implementation** unit's write set -- letting the agent
edit the tests that judge it. "The implementer may read the tests and never
write them" stops being structural the moment one file claims both.

A scenario with no file yet is fine and expected. It is in no work unit, so
it orders nothing and is **not yet expected to pass**.

### What writing them first buys

The unit implementing a contract **follows** the unit holding the scenarios
that judge it. Documentation first, tests second, code last — ordered by the
graph rather than asked for in a prompt.

Read `GET /projects/{project}/slice-context?slice=…` before writing: its
`scenarios` is what is already covered, and its `untested` is the work list.

---

## Reading

**Load the spine first.** `GET /projects/{project}/spine` returns
identifier, one-line title and all three edge lists, with no bodies. Filter
it with `?layer=B`. Then fetch only the bodies you actually need with
`GET /projects/{project}/entries/{id}`.

Retrieval is graph traversal. There is no similarity search and you should
not want one: `blast radius`, `ancestors` and `dependents` are exact answers,
and an approximate one would be worse.

Identifiers contain `·` (U+00B7). Raw or percent-encoded both work.

---

## Two health questions, reported separately

- **Sound** -- every edge lands where it should. Unsound **blocks**: writes
  are refused and no work unit is issued. Fix the graph; do not work
  around it.
- **Complete** -- knowledge has reached spec on every branch. Incomplete
  **never blocks**. It is the to-do list, and `unserved` findings are that
  list rather than defects.

---

## An assumption that is not an issue is a bug

You will hit things you cannot derive: a parent that does not say enough, an
ambiguous contract, a requirement that wants something nobody wrote down.

`POST /projects/{project}/issues` against the entry it is about, state the
assumption in the body of what you write, and carry on. Never block, never
wait, and never message another agent -- there is no mechanism for it and
there deliberately isn't one.

Silently guessing is the failure this rule exists to prevent. Anything that
only ever reports confidence makes every gate downstream theatre.

An issue against an entry no slice owns -- intent, most often -- is not a
defect anyone can repair. It is a question for whoever asked, and it comes
back as an escalation rather than as work.

---

## Amendments are append-only

Nothing is edited in place. A superseding entry carries a marker and the
original stays. Identifiers are never reused and never renumbered, and they
encode **layer and creation order only, never slice** -- which is what lets a
slice split later without renumbering anything.
