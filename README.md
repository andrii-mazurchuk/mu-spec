# mu-spec

A memory unit in a holonic-node harness. It owns the **derivation graph** of a
project: the layered specification that agent-built software is derived from,
and the edges proving what derives from what.

The problem it exists to solve is not that agents write bad code — it's that
agents write code whose justification is invisible, so no human can tell a
correct implementation of a wrong decision from an incorrect implementation of
a right one. Requiring every artifact to declare what it derives from makes
the blast radius of any change mechanically computable, and that is what makes
human review affordable.

Full architecture: **`docs/DESIGN.md`**. Read it before writing code here.

## The shape

Five layers — intent, behaviour, architecture, implementation spec, code.
Every entry carries an identifier, a derives-from list, and a body. Two
structural fields turn a pile of markdown into a directed graph, and the graph
is the whole system.

## Asking it for something

**Everything from outside goes through one door: `POST /inbox`.** A request
says what someone wants; it never writes an entry and never names a layer.
The request's `type` decides how deep a change may reach:

| type | may originate at | |
|---|---|---|
| `initiate` | intent | start a project from a raw idea |
| `feature` | intent | something the product does not do yet |
| `correction` | intent, behaviour | something is wrong |
| `comment` | nothing | an observation attached to part of the design |
| `question` | nothing | needs an answer, not a change |

`targets` is optional and usually omitted — whoever is asking generally
cannot know how the design is laid out, which is why they are asking.

There is deliberately no way to edit the spec directly. Patching something
low while the layers above still say the old thing is how the artifacts
start lying, so a correction enters at intent or behaviour and is carried
down from there.

The pipeline's own write path is separate: `submit_amendment` reaches any
layer, but must cite the request it serves. An amendment nobody asked for is
refused, so every entry traces out past the graph to the person who wanted
it.

## The boundary

**mu-spec computes. It never reasons, and it never executes.**

Every operation is deterministic and derivable from the graph: storage,
identifier permanence, spine generation, retrieval by identifier, the three
admission gates, blast radius, spec-diff → write/read set. Anything needing
judgement — authoring entries, classifying a correction to its layer,
declaring what could not be derived — belongs to the processing unit, which
calls this one.

That split is deliberate. `docs/DESIGN.md` §6 calls admission gates
"mechanical, run by the agent, human sees only failures" — and a mechanical
check doesn't need an agent, it needs a function. Putting them here means a
session in a hurry cannot skip them.

## Invariants it guards

Enforced, not documented. Violating one is a hard error, not a warning.

- Identifiers are never reused and never renumbered.
- Identifiers encode layer and creation order only, never slice. Slice
  membership is a **set** in the manifest, never a range — a split
  redistributes membership and every entry keeps the identifier it was born
  with.
- Amendments are append-only, with a superseding marker.
- Slices split, never merge.
- Cross-cutting entries are read-only from a slice.

## Two settled decisions

- **No vector search, no embeddings.** Retrieval is graph traversal:
  deterministic, cheap, explainable. "I loaded `A·14` because `B·22` derives
  from it" is auditable; "the retriever ranked it 0.83" is not. This is why
  the unit has zero runtime dependencies.
- **History is never loaded by default.** It exists for reconciliation and
  audit; keeping it out of the default read path means a session doesn't pay
  for every past mistake.

## Seeing it work

```bash
python walkthrough.py                 # runs the whole pipeline, prints every stage
python walkthrough.py --keep ./demo   # leave the files behind to read
```

It starts a real server on a real socket and makes real HTTP requests --
nothing is stubbed. It walks intent → behaviour → architecture → spec across
two slices, shows the gate refusing an unsound amendment, issues a work
package, splits a slice, and prints what ended up on disk. Read it top to
bottom and you can see exactly what the unit does and judge whether the shape
is right.

## Status

Working end to end. Storage, the graph, two admission gates, and the six
operations are implemented and served over HTTP.

Not built yet: the third gate (backwards dependency arrows between slices),
spec-level diffs so a work package can carry only what *changed* rather than
a whole slice, and the code layer's module backlinks. Per-layer field shapes
(`docs/DESIGN.md` §11) are still open and deliberately do not block anything
— the graph needs only `id`, `derives_from` and `body`, and the shapes
constrain what goes inside a body.

## Commands

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_server.py::test_health_returns_ok   # single test

python -m mu_spec.main                                # defaults to :9006
python -m mu_spec.main --port 9106                    # override
```

Configuration comes from the environment the gateway injects at process start:
`MU_SPEC_HOST`, `MU_SPEC_PORT`, `MU_SPEC_PROMPTS_DIR`.

## Registering it

Add to the gateway's `units.yaml`:

```yaml
  - name: mu-spec
    source:
      git_url: https://github.com/andrii-mazurchuk/mu_spec.git
      ref: main
    local_path: ../units/mu-spec
    lifecycle: persistent
    base_url: http://127.0.0.1:9006
    unit_type: memory
    start_cmd: ["python3", "-m", "mu_spec.main"]
    prompts:
      - path: prompts/default.md
        tier: default
        consumers: []
```

`capabilities:` stays empty until real endpoints exist; `consumers:` gets
filled in once it's decided which units should learn about mu-spec when
assembling their own context.

## Development

See `CLAUDE.md` — self-contained on purpose, including the system standard
this unit implements. **This repo is developed in isolation; do not read other
units' source.**
