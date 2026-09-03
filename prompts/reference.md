# Intake interview — reference

**Status: scaffold. The output contract below is settled and enforced. The
interview technique is not, and is deliberately left to be filled in.**

Fetched on demand by an agent that has picked up an `initiate` request from
this unit's inbox and is about to turn a raw idea into intent entries. It is
a `reference` tier prompt: no peer loads it by default, and nothing else in
the pipeline depends on its contents.

---

## When this runs

Exactly one place: an `initiate` request has arrived, a project has been
created, and no intent entries exist yet.

It is **not** the inbox. The inbox is how change arrives at a system that
already exists — a feature, a correction, a comment against a design someone
can already read. This is the other thing: there is no design yet, the
requester has an idea, and somebody has to find out what they actually mean
before a single entry is written.

Corrections and features never run this. They arrive against existing
structure, and the agent handling them reads the spine to find what they
touch.

---

## What it must produce

This half is settled, because it is what the unit enforces.

The interview ends by submitting **one amendment containing intent entries
only**, citing the `initiate` request. Concretely:

```
POST /projects/{project}/amendments
{ "in_response_to": "msg-0001",
  "entries": [ {"layer": "I", "title": "...", "body": "..."} , ... ] }
```

Constraints that are not negotiable, and why:

- **Intent entries only.** An `initiate` request may originate at `I` and
  nowhere else. An interview that produces behaviour has skipped the step
  where a human could still disagree cheaply.
- **Every entry is a problem, not a solution.** Intent is the buyer's
  problem in the buyer's terms. "Search must use an index" is an
  architectural decision wearing an intent costume, and once it is at the
  top of the graph nothing below can contradict it.
- **Titles are one line and testable-ish.** They are what everyone reads in
  the spine forever. `Buyers can find the right seller quickly` — not
  `Search`.
- **Bodies carry the why.** What breaks today, for whom, and what it costs.
  This is the only layer where that context can live.
- **Nothing is invented.** Anything the requester did not say and was not
  asked is not intent. If it matters and they were not asked, ask.

After submission every intent entry will report as `unserved`. That is
correct and expected — it is the to-do list, not a defect.

---

## What is still open

The interview *technique* is being researched separately. This section is
the placeholder, and what lands here should answer at least:

- **How many questions, and when to stop.** An interview that never
  terminates is as useless as one that never happens.
- **How to detect thin intent.** "Build me a marketplace" needs
  interrogation; a three-page brief needs summarising. Same skill, opposite
  behaviour.
- **What must be asked versus what may be assumed.** Anything assumed has to
  be flagged as a judgement call rather than quietly absorbed — an agent
  that only ever reports confidence makes every gate downstream theatre.
- **How to split one idea into several intent entries.** One `initiate`
  request almost never means one intent entry, and where the seams go
  decides what the rest of the pipeline can slice cleanly.
- **When to refuse.** Some ideas are too vague to start, and saying so is a
  better outcome than a graph built on guesses.

### Where the multi-unit architecture bites

Worth settling before the technique is written, because it constrains it:

- **mu-spec cannot conduct the interview.** It stores and computes; it does
  not reason and has no way to talk to a person. The agent that runs this
  lives elsewhere and calls in.
- **Whoever talks to the human owns the transport.** This unit never learns
  who the user is or how they were reached; it sees a request with an
  `origin` string and nothing more. Keep it that way — the moment this unit
  knows about chat threads it is coupled to whichever unit provides them.
- **The interview may need several turns, and the inbox is one-shot.** A
  request arrives, an agent asks four questions, the human answers over an
  hour. Where that conversation is held is an open question: it is not in
  this unit, and it probably should not be. One option is that the
  interviewing agent holds it and only writes here at the end; another is a
  `question`-type request per open point, answered by further requests.
  Undecided.

---

## Adding the technique

Replace the "What is still open" section with the real procedure. Nothing
else needs to change: this file is already served at
`GET /prompts/reference`, already declared in the unit's manifest entry, and
already linked from the default prompt, so a finished version is live the
moment it is written.
