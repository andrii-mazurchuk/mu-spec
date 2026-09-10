# mu-spec — insights

What is worth a periodic analytical judgment about mu-spec, and how to read
its `/stats` honestly. Written for the Analytical Unit's own review session.
See `default.md` / `reference.md` for what this unit does and `wayfinding`
for how an effort is charted against it; this is about what is worth
*watching*.

## What this unit holds

mu-spec owns the derivation graph of a project: a layered specification,
plus the edges proving what derives from what. Its metrics are all
structural facts about that graph. None of them is a health metric in the
ordinary sense, and reading them as though they were is how a wrong
judgment gets written about this unit.

## `projects: 0` means idle by design, not broken

An all-zero `/stats` means no project has been initiated here. This unit
does nothing on a schedule and nothing on its own initiative — it responds
to requests. An empty graph on a node where nobody has started a project is
the correct state.

Do not escalate an empty graph. Note it once and move on.

## The metrics that actually carry a signal

**`unimplemented_spec`** — specification entries with no module implementing
them. Some is normal: specification legitimately runs ahead of code. A
figure that *keeps growing across cycles while `modules` stays flat* is the
finding: the graph is accumulating intent nobody is building. That is the
single most useful thing you can say about this unit.

**`open_issues` and `semantic_issues`** — raised against the graph and not
yet closed. `semantic_issues` is the sharper of the two: it means the
specification contradicts itself or its own derivation, which does not
resolve by writing more code. A rising `semantic_issues` count is worth
escalating; a rising `open_issues` count with flat `semantic_issues` is
ordinary work in progress.

**`corrections_by_layer`** — where the derivation keeps getting revised.
This is the most interesting field here and the least obvious. A layer
attracting repeated corrections is a layer where the thinking is unstable —
the specification keeps being rewritten because the underlying question was
never settled. Name the layer specifically; "corrections are up" without
saying *where* is not a usable finding.

**`projects_sound` and `projects_complete`** against `projects` — the ratio
tells you whether projects are reaching a finished state or accumulating in
a partial one. Several projects, none sound, across multiple cycles, is a
pattern worth naming.

**`change_locality_mean`** — `null` until there is enough history. Read it
as "how contained is a typical change." A falling value means edits are
rippling further through the graph than they used to, which usually means
the layering is not holding. Treat a `null` as absence of data, never as
zero.

**`max_wave_depth`** and **`chain_projects`** — structural shape. Slow-
moving; interesting only when they change.

## Cadence honesty

This unit changes on the scale of weeks, not days. If it is reviewed on a
short cadence, most cycles will genuinely have nothing new, and saying so
plainly is the correct output. Do not treat an unchanged graph as a gap in
your own analysis to be filled with commentary.

## What you cannot see

- **Entry content.** You get counts by layer, never what any entry says.
- **Who changed what, or when.** No per-entry timestamps, no authorship.
- **Whether the specification is any good.** Soundness here is a structural
  property of the graph, not a judgment about whether the thing specified is
  worth building.
- **Which project a number belongs to.** Every figure is aggregated across
  all projects.

Note also that this unit reports `computed_at` as a Unix epoch float while
the other units report an ISO-8601 string. Convert before comparing; do not
report it raw.
