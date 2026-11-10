import { useEffect, useState, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import { useScreenState, type ScreenState } from "@/lib/screen-state"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { ProjectPicker, SearchBox, Freshness, ViewNav, SliceList, StatusLine, PhoneNote, HelperPanel, type StatusItem } from "@/components/ui/shell"
import { KeyValue, StepPanel, Callout, EntryArticle, StateBlock, LoadingVeil, RunButton, type BlockState, type RunPhase } from "@/components/ui/blocks"
import { SpineGraph, chainOf, type EdgeKind, type SpineData } from "@/components/ui/spine-graph"
import { FileTree, countFiles, type Folder } from "@/components/ui/file-tree"
import { WaveTimeline } from "@/components/ui/wave-timeline"
import { RunProgress } from "@/components/ui/run-progress"
import { phaseOf, POLL_MS, useRun, watchRun } from "@/lib/run"
import * as F from "@/fixtures/mu"

// The seven views of the chosen wireframes (all option A), in grey, from the gallery's elements and the sample data.
// Block names match 03-wireframes/SCR-<id>.A.html. ?state=… (review.html only) renders each brief state; a state
// changes what the block shows and what can be done, in the block it affects.

/** One hash query value, re-read on every hash change. */
function useQuery(key: string): string | null {
  const read = () => new URLSearchParams(location.hash.split("?")[1] ?? "").get(key)
  const [v, setV] = useState(read)
  useEffect(() => { const on = () => setV(read()); addEventListener("hashchange", on); return () => removeEventListener("hashchange", on) }, [key])
  return v
}
const go = (h: string) => { location.hash = h }
/** Retry and refresh reload the view without the review state. */
const reload = () => { const [path, query = ""] = location.hash.split("?"); const p = new URLSearchParams(query); p.delete("state"); go(`${path}${p.size ? `?${p}` : ""}`) }
const search = (q: string) => {
  const id = /^([IBAST])\s*[·.]?\s*(\d{1,3})$/i.exec(q)
  go(id ? `#/spine?entry=${id[1].toUpperCase()}·${id[2].padStart(2, "0")}` : `#/read?q=${encodeURIComponent(q)}`)
}
const link = "rounded-sm text-foreground underline underline-offset-4 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
const LAST_GOOD = <span className="whitespace-nowrap">today 14:02</span>
/** Empty: counts read 0, or a dash where a word stood. */
const zeroed = (items: StatusItem[]): StatusItem[] => items.map(x => ({ n: /^\d/.test(x.n) ? "0" : "—", label: x.label }))

/** Ship a sample run: there is no live backend here, so each 0.9 s poll reads the next fixture snapshot. ?state=running
 *  (review only) starts the full-cut sample on any view; ?run=phase1|phase2|trouble|clean|failed starts it there. */
function sampleRun(plan: F.RunPlan, from = 0) {
  const r = F.shipRun(plan)
  let tick = ({ phase1: r.mid1, phase2: r.mid2, trouble: r.end, clean: r.end, failed: r.end } as Record<string, number>)[from] ?? from
  watchRun(() => r.at(tick++), Date.now() - tick * POLL_MS)
}

// ---------- frame ----------
function Frame({ view, state, idea, phone = false, rail, status, emptyStatus, helper, children }: {
  view: string; state: ScreenState; idea: string; phone?: boolean; rail?: ReactNode; status?: StatusItem[]; emptyStatus?: StatusItem[]; helper?: ReactNode; children: ReactNode
}) {
  const name = F.VIEWS.find(([v]) => v === view)![1]
  const run = useRun(), at = useQuery("run")
  useEffect(() => { if (state === "running") sampleRun({ total: 120, toWire: 210, outcome: at === "clean" ? "clean" : at === "failed" ? "failed" : "trouble" }, at as never) }, [state, at])
  return (
    <main data-screen={view} className="mx-auto flex max-w-[1440px] flex-col gap-4 px-4 py-3 sm:px-6 sm:py-4">
      <div className="flex flex-wrap items-center gap-4">
        <div data-block="project"><ProjectPicker projects={F.PROJECTS} project={F.PROJECT} forks={F.FORKS} fork="main" /></div>
        <div data-block="search" className="min-w-0 flex-1 max-sm:order-first max-sm:basis-full"><SearchBox onSearch={search} /></div>
        <div data-block="freshness"><Freshness updated={F.UPDATED} variant={state === "stale" ? "stale" : state === "offline" ? "offline" : "default"} onRefresh={reload} /></div>
      </div>
      <h1 className="m-0 text-xl font-semibold text-foreground">{name} · {F.PROJECT}</h1>
      <p data-block="idea" className="m-0 text-sm text-muted-foreground">{idea}</p>
      {phone && <div data-block="phone-note" className="sm:hidden"><PhoneNote /></div>}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <div className="flex flex-col gap-4 sm:w-[220px] sm:flex-none">
          <div data-block="views"><ViewNav views={F.VIEWS} current={view} shipping={run?.e.running ? phaseOf(run.e).pct : undefined} phase={run?.e.running && phaseOf(run.e).phase === 2 ? "order" : "issues"} /></div>
          {rail ?? <div data-block="slices" className="max-sm:hidden">
            {state === "empty" ? <p className="m-0 text-sm text-muted-foreground">No slices yet.</p> : <SliceList title="Slices" slices={F.SLICES} />}
          </div>}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {status && <div data-block="status"><StatusLine items={state === "empty" ? emptyStatus ?? zeroed(status) : status} loading={state === "loading"} /></div>}
          {children}
        </div>
        {helper}
      </div>
    </main>
  )
}

/** The helper panel: nothing selected when the view is empty; values greyed while loading. */
/** `explains`: the panel says what the view does, so it stays in every state. */
function Helper({ state, title, swapKey, children, first = false, explains = false }: { state: ScreenState; title: ReactNode; swapKey?: string; children: ReactNode; first?: boolean; explains?: boolean }) {
  const blank = !explains && (state === "empty" || state === "error")
  return (
    <HelperPanel data-block="helper" title={blank ? "Nothing selected" : title} swapKey={blank ? undefined : swapKey} className={cn("sm:sticky sm:top-4 sm:max-h-[calc(100dvh-2rem)] sm:w-[300px] sm:flex-none sm:overflow-y-auto sm:overscroll-contain", first && "max-sm:order-first")}>
      {blank ? <p className="m-0 text-muted-foreground">{state === "error" ? "Nothing to show: the data behind this panel could not be loaded." : "Nothing is selected: this project has no data for this view yet."}</p> : <LoadingVeil on={state === "loading" && !explains}>{children}</LoadingVeil>}
    </HelperPanel>
  )
}

type Notes = Partial<Record<BlockState, { text?: ReactNode; action?: ReactNode }>>
/** The block a state lands in. Empty and error show only the state's sentence and its next action; loading greys the
 *  values; the rest keep the data under a note. A state outside `handles` leaves the block as it is. */
function Target({ name, state, what, handles = ["empty", "loading", "error", "stale", "partial", "offline"], notes = {}, errorKeeps, className, children }: {
  name: string; state: ScreenState; what: string; handles?: BlockState[]; notes?: Notes; errorKeeps?: ReactNode; className?: string; children: ReactNode
}) {
  const st = state !== "default" && state !== "running" && handles.includes(state) ? state : null
  const retry = <Button variant="outline" size="sm" onClick={reload}>Retry</Button>
  const DEFAULTS: Record<BlockState, { text: ReactNode; action?: ReactNode }> = {
    empty: { text: `No ${what} yet.` },
    loading: { text: `Loading ${what}; labels stay, values fill in as they arrive.` },
    error: { text: <>{what}. Last good data {LAST_GOOD}.</>, action: retry },
    stale: { text: `Updated 3 min ago; newer ${what} may exist.`, action: <Button variant="outline" size="sm" onClick={reload}>Refresh</Button> },
    partial: { text: `Some ${what} are incomplete; each row says what it lacks.` },
    offline: { text: <>Showing {what} from {LAST_GOOD}, not current.</> },
    permission: { text: "No access." },
  }
  const n = st && { ...DEFAULTS[st], ...notes[st] }
  return (
    <section data-block={name} className={cn("flex min-w-0 flex-col gap-3", className)}>
      {st && n && <div data-block={`state-${st}`}><StateBlock variant={st} action={n.action}>{n.text}</StateBlock></div>}
      {st === "empty" ? null : st === "error" ? errorKeeps ?? null : <LoadingVeil on={st === "loading"}>{children}</LoadingVeil>}
    </section>
  )
}

/** A block that is not a state target: empty says so in a line, loading greys the values. */
function Plain({ state, empty, children }: { state: ScreenState; empty: string; children: ReactNode }) {
  if (state === "empty") return <p className="m-0 text-sm text-muted-foreground">{empty}</p>
  return <LoadingVeil on={state === "loading"}>{children}</LoadingVeil>
}

function H2({ children }: { children: ReactNode }) { return <h2 className="m-0 text-base font-semibold text-foreground">{children}</h2> }
/** A panel surface: lists, history and the reading column sit on it, apart from the page background. */
function Surface({ children, className, ...rest }: React.ComponentProps<"div">) {
  return <div className={cn("flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 text-card-foreground", className)} {...rest}>{children}</div>
}
/** An entry id in the mono face (J03). */
function Id({ children }: { children: ReactNode }) { return <span className="font-mono">{children}</span> }
/** A status word with the node's meaning as a small mark beside it; the word stays foreground (J04). */
function Tone({ tone, children }: { tone?: "warn" | "ok" | "down"; children: ReactNode }) {
  const bg = { warn: "bg-status-warn", ok: "bg-status-ok", down: "bg-status-down" }
  return <span className="whitespace-nowrap">{tone && <span aria-hidden className={cn("mr-1.5 inline-block size-2 rounded-full align-[1px]", bg[tone])} />}{children}</span>
}
function DataTable({ head, rows, num = [], caption, selected }: { head: string[]; rows: ReactNode[][]; num?: number[]; caption: string; selected?: number }) {
  return (
    <Table aria-label={caption}>
      <TableHeader><TableRow>{head.map((h, i) => <TableHead key={h} scope="col" className={cn(num.includes(i) && "text-right")}>{h}</TableHead>)}</TableRow></TableHeader>
      <TableBody>{rows.map((r, i) => <TableRow key={i} data-state={i === selected ? "selected" : undefined} className={cn(i === selected && "bg-primary/15 data-[state=selected]:bg-primary/15 [&>td:first-child]:border-l-[3px] [&>td:first-child]:border-l-primary")}>{r.map((c, j) => <TableCell key={j} className={cn("whitespace-normal", num.includes(j) && "text-right tabular-nums")}>{c}</TableCell>)}</TableRow>)}</TableBody>
    </Table>
  )
}

// ---------- Spine ----------
/** Spec entries carry their test count on the graph; tests themselves are never drawn. */
const tested = (id: string) => { const n = F.TESTS.filter(t => t.verifies === id).length; return n ? ` · ${n} test${n === 1 ? "" : "s"}` : "" }
const SPINE: SpineData = { layers: F.LAYERS, edges: F.EDGES, nodes: F.SPINE_NODES.map(([id, t, s]) => [id, id.startsWith("S·") ? t + tested(id) : t, s]) }
const layerOf = (id: string) => (id.includes("·") ? id[0] : "M")
/** Everything that derives from the entry, down every layer (its blast radius). */
const downFrom = (id: string) => { const out = new Set<string>(), stack = [id]; while (stack.length) { const n = stack.pop()!; for (const [a, b, k] of F.EDGES) if (a === n && !k && !out.has(b)) { out.add(b); stack.push(b) } } return out }
const EDGE_KINDS: [EdgeKind, string][] = [["derives", "derives from"], ["parent", "parent of"], ["depends", "depends on"], ["contradicts", "contradicts"]]

function Spine() {
  const state = useScreenState(), entry = useQuery("entry") ?? "A·04"
  const [edges, setEdges] = useState<string[]>(["derives"]), [test, setTest] = useState<string>()
  useEffect(() => { setTest(undefined) }, [entry])
  const linked = chainOf(entry, F.EDGES), down = downFrom(entry)
  const tests = F.TESTS.filter(t => linked.has(t.verifies))
  const missing: Record<string, string> = state === "partial" ? { "S·23": "no body yet", "B·11": "slice: none" } : {}
  const layerName = Object.fromEntries(F.LAYERS) as Record<string, string>
  const byLayer = F.LAYERS.map(([k, n]) => [n, [...linked].filter(id => layerOf(id) === k)] as const).filter(([, ids]) => ids.length)
  const linkedText = byLayer.map(([n, ids]) => `${ids.length} ${n}`).join(", ") || "nothing"
  const downSpec = [...down, entry].filter(id => layerOf(id) === "S"), downFiles = [...down].filter(id => layerOf(id) === "M")
  const blastTests = F.TESTS.filter(t => downSpec.includes(t.verifies)).length
  const sel = F.TESTS.find(t => t.id === test)
  const body = missing[entry] === "no body yet" ? undefined : F.BODIES[entry]
  const slice = missing[entry] === "slice: none" ? "none" : F.SLICE_OF[entry] ?? "none (intent is not sliced)"
  const graph = (
    <div className="flex flex-col gap-3 max-sm:hidden">
      <SpineGraph data={SPINE} focus={entry} edges={edges as EdgeKind[]} missing={missing} height={640} hrefFor={id => `#/spine?entry=${encodeURIComponent(id)}`} sliceHref={s => `#/read?slice=${s}`}
        toolbar={
          <div data-block="edges" className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-foreground">Edges:</span>
            <ToggleGroup type="multiple" variant="outline" size="sm" value={edges} onValueChange={setEdges} aria-label="Edges shown">
              {EDGE_KINDS.map(([k, n]) => <ToggleGroupItem key={k} value={k}>{n}</ToggleGroupItem>)}
            </ToggleGroup>
          </div>
        } />
      <div data-block="tests" className="flex flex-col gap-2">
        <H2>Tests of the linked spec entries · {tests.length}</H2>
        {tests.length ? <DataTable caption="Tests of the linked spec entries" head={["Test", "Verifies", "Title"]} rows={tests.map(t => [
          <Button variant="link" className={cn(link, "h-auto p-0 font-mono font-normal")} aria-pressed={test === t.id} onClick={() => setTest(t.id)}>{t.id}</Button>, <Id>{t.verifies}</Id>, t.title])} selected={tests.findIndex(t => t.id === test)} />
          : <p className="m-0 text-sm text-muted-foreground">No test verifies the spec entries linked to {entry}.</p>}
      </div>
    </div>
  )
  // Phone is information only (family rule): a summary of the focus, then its chain as a list by layer.
  const chain = (
    <div data-part="chain" className="flex flex-col gap-2 sm:hidden">
      <Surface>
        <H2><Id>{entry}</Id> · {missing[entry] ?? F.TITLES[entry] ?? "entry"}</H2>
        <KeyValue rows={[["layer", `${layerName[layerOf(entry)]} · slice ${slice}`], ["linked", linkedText], ["blast radius", `${downSpec.length} spec entries, ${downFiles.length} files, ${blastTests} tests`]]} />
      </Surface>
      <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
        {byLayer.map(([n, ids]) => (
          <li key={n} className="flex flex-col gap-0.5 rounded-lg border border-border bg-card px-3 py-2">
            <span className="text-xs text-muted-foreground">{n} · {ids.length}</span>
            {ids.map(id => <span key={id} className={cn("break-words", id === entry && "font-semibold")}><Id>{id}</Id> · {missing[id] ?? F.TITLES[id] ?? "entry"}</span>)}
          </li>
        ))}
      </ul>
      <p className="m-0 text-sm text-muted-foreground">{tests.length} tests verify the linked spec entries.</p>
    </div>
  )
  return (
    <Frame view="spine" state={state} idea="Move around the graph and zoom in for names; search or pick an entry to focus its chain." status={F.SPINE_STATUS}
      emptyStatus={[...F.LAYERS.slice(0, 4).map(([, n]) => ({ n: "0", label: n })), { n: "0", label: "tests" }, { n: "0", label: "cuts taken" }]}
      helper={
        <Helper state={state} swapKey={entry} title={<><Id>{entry}</Id> · {F.TITLES[entry] ?? "entry"}</>}>
          <div className="flex flex-col gap-3">
            <KeyValue rows={[["layer", `${layerName[layerOf(entry)]} · slice ${slice}`], ["linked", linkedText], ["blast radius", `${downSpec.length} spec entries, ${downFiles.length} files, ${blastTests} tests`]]} />
            <h3 className="m-0 text-sm font-semibold">Body</h3>
            {body ? <p className="m-0">{body}</p> : <p className="m-0 text-muted-foreground">{missing[entry] ? "no body yet" : "Loaded when opened in Read (not in this sample)."}</p>}
            <a className={cn(link, "self-start")} href={`#/read?q=${encodeURIComponent(entry)}`}>Open in Read</a>
            {sel ? <div className="flex flex-col gap-1"><h3 className="m-0 text-sm font-semibold"><Id>{sel.id}</Id> · {sel.title}</h3><p className="m-0">{sel.body}</p><span className="text-xs text-muted-foreground">verifies <Id>{sel.verifies}</Id></span></div>
              : <p className="m-0 text-muted-foreground max-sm:hidden">Click a test under the graph to inspect it here.</p>}
          </div>
        </Helper>
      }>
      <Target name="graph" state={state} what="spine entries"
        notes={{ empty: { text: `The spec has no entries yet. Layers: ${F.LAYERS.map(([, n]) => n).join(", ")}, and tests beside them.`, action: <a className={link} href="#/queues">Open Queues</a> }, error: { text: <>spine data. Last good data {LAST_GOOD}; other views still work.</> } }}>
        {graph}
        {chain}
      </Target>
      <section data-block="state" className="flex flex-col gap-2">
        <H2>Spec state · how far each slice has reached</H2>
        <Plain state={state} empty="No slices yet: nothing has been drafted.">
          <DataTable caption="Spec state per slice" head={["Slice", "B", "A", "S", "T"]} rows={F.SPEC_STATE.map(r => [...r])} />
        </Plain>
      </section>
    </Frame>
  )
}

// ---------- Order ----------
function Order() {
  const state = useScreenState(), [unit, setUnit] = useState("S·04 + fx.py")
  const wave = F.WORK_WAVES.find(w => w.units.includes(unit))?.wave ?? 1
  const [id, file] = unit.split(" + "), d = F.UNIT_DETAIL[unit] ?? { writes: file, entries: id }
  const files = (d.writes ?? file).split(", ").filter(f => !f.startsWith("tests/") || f === file)
  const fileRows = files.map(f => [f, allFiles(F.TREE).find(r => r.name === f.split("/").pop())] as const)
  const unitTests = F.TESTS.filter(t => t.verifies === id)
  const prev = F.WORK_WAVES.find(w => w.wave === wave - 1)
  const recutOff = state === "offline" ? "offline" : state === "error" ? "units failed to load" : state === "empty" ? "no modules declared" : undefined
  return (
    <Frame view="order" state={state} phone idea="Work units on the wave timeline, as today; cut them here." status={F.ORDER_STATUS}
      emptyStatus={[{ n: "0", label: "work units" }, { n: "0", label: "waves" }, { n: "0", label: "shared files" }]}
      helper={
        <Helper state={state} swapKey={unit} title={<Id>{unit}</Id>}>
          <div className="flex flex-col gap-4">
            <KeyValue rows={[["wave", String(wave)], ["waits on", prev ? <span>wave {prev.wave}: <Id>{prev.units.map(u => u.split(" + ")[0]).join(", ")}</Id></span> : "nothing"], ["write set", d.writes]]} />
            <div className="flex flex-col gap-1">
              <h3 className="m-0 text-sm font-semibold">Implements</h3>
              <p className="m-0"><Id>{id}</Id> · {F.TITLES[id] ?? "spec entry"}</p>
              <p data-value="" className="m-0 text-muted-foreground">{F.BODIES[id] ?? "Body loads in Read."}</p>
              <a className={cn(link, "self-start")} href={`#/read?q=${encodeURIComponent(id)}`}>Open {id} in Read</a>
            </div>
            <div className="flex flex-col gap-1">
              <h3 className="m-0 text-sm font-semibold">{fileRows.length === 1 ? "Module" : "Modules"}</h3>
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {fileRows.map(([f, r]) => <li key={f}><span className="font-mono">{f}</span> <span className="text-muted-foreground tabular-nums">· {r ? `${r.spec} spec · ${r.test} ${r.test === 1 ? "test" : "tests"}` : "not in the tree"}</span></li>)}
              </ul>
              <a className={cn(link, "self-start")} href={`#/modules?file=${encodeURIComponent(file)}`}>Open in Modules</a>
            </div>
            <div className="flex flex-col gap-1">
              <h3 className="m-0 text-sm font-semibold">Tests · {unitTests.length}</h3>
              {unitTests.length ? <ul className="m-0 flex list-none flex-col gap-1 p-0">{unitTests.map(t => <li key={t.id}><Id>{t.id}</Id> · {t.title}</li>)}</ul>
                : <p className="m-0 text-muted-foreground">No test entries verify {id} yet.</p>}
            </div>
          </div>
        </Helper>
      }>
      <div data-block="cut" className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-foreground">
        <span className="flex flex-col gap-1 max-sm:hidden">
          <Button variant="outline" disabled={!!recutOff}>{state === "empty" ? "Cut work units" : "Re-cut work units"}{recutOff ? ` · ${recutOff === "offline" ? "offline" : "off"}` : ""}</Button>
        </span>
        {recutOff && recutOff !== "offline" && <span className="text-muted-foreground max-sm:hidden">Re-cut is off: {recutOff}.</span>}
        {state === "empty" ? <span>No cut yet: no modules are declared.</span> : <><span data-value="">{F.CHANGED}</span><span className="text-muted-foreground">last cut: cut 3, 21 Sep 14:10</span></>}
      </div>
      <Target name="waves" state={state} what="work units"
        notes={{ empty: { text: "No work units yet: the spec declares no modules.", action: <a className={link} href="#/modules">Open Modules</a> }, error: { text: <>work units. Last good data {LAST_GOOD}; Re-cut is off until they load.</> }, stale: { text: "Updated 3 min ago · 4 units moved since cut 3.", action: <Button variant="outline" size="sm" onClick={reload}>Refresh</Button> } }}>
        <WaveTimeline variant="units" selected={unit} onPick={setUnit} more="+ waves 4 and 5 · 8 units"
          waves={F.WORK_WAVES.map(w => ({ title: `Wave ${w.wave} · ${w.units.length} units`, items: w.units.map(u => ({ id: u, label: u, sub: `waits on ${w.wave === 1 ? "nothing" : `wave ${w.wave - 1}`}${state === "partial" && u.startsWith("S·14") ? " · no test entries" : ""}` })) }))} />
      </Target>
    </Frame>
  )
}

// ---------- Read ----------
function Read() {
  const state = useScreenState(), q0 = useQuery("q") ?? "", slice = useQuery("slice")
  const [q, setQ] = useState(q0), [layer, setLayer] = useState("B")
  useEffect(() => setQ(q0), [q0])
  useEffect(() => { if (slice) document.getElementById(`slice-${slice}`)?.scrollIntoView() }, [slice])
  const needle = q.trim().toLowerCase()
  const groups = F.READ_ENTRIES.map(g => ({ ...g, entries: g.entries.filter(e => !needle || `${e.id} ${e.title} ${e.body}`.toLowerCase().includes(needle)) }))
  const picked = slice && !F.READ_ENTRIES.some(g => g.slice === slice) ? F.SLICES_B.find(([s]) => s === slice) : undefined
  const LN: Record<string, string> = { I: "intent", B: "behaviour", A: "architecture", S: "spec", T: "tests" }
  return (
    <Frame view="read" state={state} idea="The corpus, entry by entry, at full width; every entry stays reachable."
      rail={<div data-block="outline" className="max-sm:hidden"><SliceList title={`Outline · ${LN[layer]}`} slices={F.SLICES_B} onPick={s => go(`#/read?slice=${s}`)} /></div>}>
      <div data-block="filter" className="flex flex-wrap items-center gap-3">
        <Input type="search" aria-label="Find in corpus" placeholder="Find S·04 or a word" value={q} onChange={e => setQ(e.target.value)} className="w-[280px] max-w-full" />
        <ToggleGroup type="single" variant="outline" size="sm" value={layer} onValueChange={v => v && setLayer(v)} aria-label="Layer">
          {Object.entries(LN).map(([k, n]) => <ToggleGroupItem key={k} value={k} aria-label={n}>{k}</ToggleGroupItem>)}
        </ToggleGroup>
        <span className="text-sm text-muted-foreground">{state === "empty" ? "0" : F.LAYER_COUNT[layer] ?? 8} entries · {LN[layer]}</span>
      </div>
      <Target name="entries" state={state} what="entries"
        notes={{ empty: { text: `The ${LN[layer]} layer has no entries yet.`, action: <Button variant="outline" size="sm" onClick={() => setLayer(layer === "B" ? "I" : "B")}>Show {layer === "B" ? "intent" : "behaviour"}</Button> },
          error: { text: <>the {LN[layer]} layer (timed out). Last good data {LAST_GOOD}; loaded layers stay readable.</> },
          stale: { text: "5 entries changed since this text loaded.", action: <Button variant="outline" size="sm" onClick={reload}>Reload changed entries</Button> } }}>
        {layer !== "B" ? <p className="m-0 text-sm text-muted-foreground">Loading {LN[layer]} entries…</p> : <>
          {picked && <div id={`slice-${picked[0]}`} className="flex flex-col gap-1"><H2>{picked[0]} · {picked[1]} behaviour entries</H2><p className="m-0 text-sm text-muted-foreground">Loading {picked[0]}…</p></div>}
          {groups.map(g => (
            <section key={g.slice} id={`slice-${g.slice}`} className="flex scroll-mt-4 flex-col gap-3">
              <H2>{g.slice} · {g.total} behaviour entries</H2>
              {g.entries.map((e, i) => <EntryArticle key={e.id} id={e.id} title={e.title} body={state === "partial" && i === 2 ? undefined : e.body} meta={`derives from ${e.from} · slice ${g.slice}`} />)}
              {!g.entries.length && <p className="m-0 py-2 text-sm text-muted-foreground">No entry in {g.slice} matches "{q}".</p>}
              <p className="m-0 text-sm text-muted-foreground">+ 5 more in capture · loading conversation…</p>
            </section>
          ))}
        </>}
      </Target>
    </Frame>
  )
}

// ---------- Waves ----------
function Waves() {
  const state = useScreenState()
  return (
    <Frame view="waves" state={state} idea="Slices by the wave they can be written in."
      helper={<Helper state={state} title="About Waves" explains><p className="m-0">The writing order, from slice dependencies. Build order is in <a className={link} href="#/order">Order</a>.</p></Helper>}>
      <Target name="waves" state={state} what="waves" notes={{ empty: { text: "No slices yet, so there are no writing waves.", action: <a className={link} href="#/spine">Open Spine</a> } }}>
        <WaveTimeline variant="slices" waves={F.SLICE_WAVES.map(w => ({ title: `Wave ${w.wave}`, items: w.slices.map(s => ({ id: s.name, label: s.name, href: `#/read?slice=${s.name}`,
          sub: `${s.size} entries · after ${s.after} · ${state === "partial" && s.name === "outreach" ? "cohesion pending" : `cohesion\u00a0${s.cohesion}`}` })) }))} />
      </Target>
    </Frame>
  )
}

// ---------- Modules ----------
const allFiles = (d: Folder): Folder["files"] => [...d.files, ...d.folders.flatMap(allFiles)]
const withoutSpec = (d: Folder): Folder => ({ ...d, files: d.files.map(f => (f.name === "Caddyfile" ? { ...f, spec: 0 } : f)) })
function Modules() {
  const state = useScreenState(), fileQ = useQuery("file"), [file, setFile] = useState(fileQ ?? "server.py")
  useEffect(() => { if (fileQ) setFile(fileQ) }, [fileQ])
  const row = allFiles(F.TREE).find(f => f.name === file)
  const det = F.FILE_DETAIL[file] ?? { slices: "capture", spec: row && row.spec ? [`${row.spec} entries`] : [], tests: row && row.test ? [`${row.test} entries`] : [], flags: [] }
  return (
    <Frame view="modules" state={state} idea="Fold and unfold folders; click a file to see it on the right." status={F.MOD_STATUS}
      helper={
        <Helper state={state} swapKey={file} title={file}>
          <div className="flex flex-col gap-2">
            <KeyValue rows={[["slices", det.slices], ["spec entries", det.spec.length ? <span className="flex flex-wrap gap-x-2">{det.spec.map(s => <a key={s} className={cn(link, "font-mono")} href={`#/spine?entry=${encodeURIComponent(s)}`}>{s}</a>)}</span> : "none"],
              ["test entries", det.tests.length ? <Id>{det.tests.join(" ")}</Id> : "none"], ["flags", det.flags.length ? <span className="flex flex-wrap gap-1">{det.flags.map(x => <Badge key={x} variant="outline">{x}</Badge>)}</span> : "none"]]} />
            <p className="m-0 text-muted-foreground">Prose about this file belongs to the generated page.</p>
          </div>
        </Helper>
      }>
      <Target name="tree" state={state} what="files" notes={{ empty: { text: "No files declared yet.", action: <Button variant="outline" size="sm" onClick={reload}>Refresh</Button> } }}>
        <H2>Files · {countFiles(F.TREE)} <span className="font-normal text-muted-foreground">· click a folder to fold it</span></H2>
        <FileTree root={state === "partial" ? withoutSpec(F.TREE) : F.TREE} selected={file} onPick={setFile} />
      </Target>
    </Frame>
  )
}

// ---------- Queues ----------
type Sort = "newest" | "undecided" | "id"
const sorted = <T extends { id: string; status: string; order: number }>(xs: T[], s: Sort) => [...xs].sort((a, b) =>
  s === "newest" ? b.order - a.order : s === "undecided" ? Number(!["pending", "open"].includes(a.status)) - Number(!["pending", "open"].includes(b.status)) || b.order - a.order : a.id.localeCompare(b.id, undefined, { numeric: true }))
function Queues() {
  const state = useScreenState(), listQ = useQuery("list")
  const [sort, setSort] = useState<Sort>("newest"), [req, setReq] = useState("R·10")
  const list = listQ === "issues" ? "issues" : "requests"
  const r = F.REQUESTS.find(x => x.id === req)!
  const off = state === "offline"
  const requests = (
    <div className="flex flex-col gap-2">
      <H2>Requests · 2 pending</H2>
      {state === "error" ? null : <>
        <DataTable caption="Requests" head={["Id", "Type", "Title", "Status", "At"]} rows={sorted(F.REQUESTS, sort).map(x => [
          <Button variant="link" className={cn(link, "h-auto p-0 font-mono font-normal")} aria-pressed={req === x.id} onClick={() => setReq(x.id)}>{x.id}</Button>, x.type, x.title, <Tone tone={x.status === "pending" ? "warn" : undefined}>{x.status}</Tone>, x.at])}
          selected={sorted(F.REQUESTS, sort).findIndex(x => x.id === req)} />
        <p className="m-0 text-sm text-muted-foreground">+ {F.REQUESTS_TOTAL - F.REQUESTS.length} more</p>
      </>}
    </div>
  )
  const issues = (
    <div className="flex flex-col gap-2">
      <H2>Issues · {F.ISSUES_OPEN} open</H2>
      <DataTable caption="Issues" head={["Id", "Target", "Slice", "Kind", "Claim", "Status"]} rows={sorted(F.ISSUES, sort).map(x => [<Id>{x.id}</Id>, <Id>{x.target}</Id>, state === "partial" && x.id === "X·07" ? "none" : x.slice, x.kind, x.claim, x.status])} />
      <p className="m-0 text-sm text-muted-foreground">+ {F.ISSUES_TOTAL - F.ISSUES.length} more, 2 of them open</p>
    </div>
  )
  const switcher = (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <ToggleGroup type="single" variant="outline" value={list} onValueChange={v => v && go(`#/queues?list=${v}${state !== "default" ? `&state=${state}` : ""}`)} aria-label="List shown">
        <ToggleGroupItem value="requests">Requests {F.REQUESTS_TOTAL}</ToggleGroupItem>
        <ToggleGroupItem value="issues">Issues {F.ISSUES_TOTAL} · {F.ISSUES_OPEN} open</ToggleGroupItem>
      </ToggleGroup>
      <span className="text-muted-foreground">sort:</span>
      <ToggleGroup type="single" variant="outline" size="sm" value={sort} onValueChange={v => v && setSort(v as Sort)} aria-label="Sort">
        <ToggleGroupItem value="newest">newest</ToggleGroupItem>
        <ToggleGroupItem value="undecided">undecided first</ToggleGroupItem>
        <ToggleGroupItem value="id">id</ToggleGroupItem>
      </ToggleGroup>
    </div>
  )
  return (
    <Frame view="queues" state={state} phone idea="Jump to Requests or Issues; each list sorts by time, status or id." status={F.QUEUE_STATUS}
      helper={<Helper state={state} swapKey={r.id} title={<Id>{r.id}</Id>}><div className="flex flex-col gap-2"><KeyValue rows={[["type", r.type], ["status", <Tone tone={r.status === "pending" ? "warn" : undefined}>{r.status}</Tone>], ["received", r.at]]} /><p className="m-0">{r.title}.</p></div></Helper>}>
      <div data-block="proposal">
        {state === "empty" ? <p className="m-0 text-sm text-muted-foreground">No slicing proposal is waiting.</p> : (
          <LoadingVeil on={state === "loading"}>
            <Callout actions={<span className="flex gap-2 max-sm:hidden"><Button variant="outline" size="sm" disabled={off}>{off ? "Ratify · offline" : "Ratify"}</Button><Button variant="outline" size="sm" disabled={off}>{off ? "Reject · offline" : "Reject with note"}</Button></span>}>
              <Tone tone="warn"><b>A slicing proposal is waiting on you</b></Tone> · score 0.82 · 3 waves
            </Callout>
          </LoadingVeil>
        )}
      </div>
      <Target name="lists" state={state} what="requests and issues"
        notes={{ empty: { text: "No pending requests. No open issues.", action: <Button variant="outline" size="sm" onClick={reload}>Refresh</Button> }, error: { text: <>requests. Last good data {LAST_GOOD}; Issues still work.</> } }}
        errorKeeps={<Surface>{switcher}{list === "issues" ? issues : requests}</Surface>}>
        <Surface>
          {switcher}
          {list === "issues" ? issues : requests}
        </Surface>
      </Target>
    </Frame>
  )
}

// ---------- Ship ----------
function Ship() {
  const state = useScreenState()
  const [cut, setCut] = useState<RunPhase>("idle"), [ship, setShip] = useState<RunPhase>("idle"), [cutDone, setCutDone] = useState(false)
  const run = useRun(), running = !!run?.e.running, landed = !!run && !running
  const off = state === "offline" ? "offline" : undefined
  // Units changed since the last cut: shipping now would ship the old cut, so Ship waits for the new cut (J08).
  // A run in flight keeps the button pending ("Shipping…"), never a reason.
  const shipOff = running ? undefined : off ?? (state === "permission" ? "No GitHub token set" : state === "empty" ? "Ship · take the first cut" : !cutDone ? "Ship · take the new cut first" : undefined)
  const create = cutDone ? 3 : 0
  const S = F.SHIP_SETTINGS
  const settings = (
    <>
      <div className="grid grid-cols-[max-content_minmax(0,1fr)] items-center gap-x-3 gap-y-2 text-sm max-sm:hidden">
        <Label htmlFor="ship-repo">Repo</Label><Input id="ship-repo" defaultValue={S.repo} autoCapitalize="none" autoCorrect="off" />
        <Label htmlFor="ship-labels">Labels</Label><Input id="ship-labels" defaultValue={S.labels} />
        <Label htmlFor="ship-pickup">Pickup labels</Label><Input id="ship-pickup" defaultValue={S.pickup} />
        <Label htmlFor="ship-horizon">Horizon</Label>
        <NativeSelect id="ship-horizon" size="sm" defaultValue={S.horizon} className="w-full">{S.horizons.map(h => <NativeSelectOption key={h} value={h}>{h}</NativeSelectOption>)}</NativeSelect>
        <span className="col-span-2 flex items-center gap-2"><Checkbox id="ship-delegate" defaultChecked={S.delegate} /><Label htmlFor="ship-delegate">Delegate to agents</Label></span>
      </div>
      <p className="m-0 text-sm text-muted-foreground sm:hidden">{S.repo} · {S.horizon}</p>
    </>
  )
  const shipSide = (
    <>
      <LoadingVeil on={state === "loading"}><div className="flex flex-col gap-2">
      {state === "empty" ? <>
        <span>Nothing to ship: no cut is taken yet.</span>
        <span>After the first cut, <b data-value="">17</b> issues would be created in <b data-value="">5</b> waves.</span>
      </> : <>
      <span>Ships cut {cutDone ? 4 : 3}: <b data-value="">{landed ? 0 : create}</b> issues would be created now</span>
      {!cutDone && <span className="text-muted-foreground">{F.CHANGED}: take the new cut first.</span>}
      {!cutDone && <span><b data-value="">3</b> issues will be created after the new cut</span>}
      <span><b data-value="">0</b> held back · <b data-value="">1</b> new wave of parallel work</span>
      </>}
      <span className="text-muted-foreground">{state === "permission" ? "No GitHub token set" : `token: ${S.token}`}</span>
      </div></LoadingVeil>
      <div className="max-sm:hidden">
        <RunButton kind="ship" phase={running ? "pending" : ship} count={create} disabledReason={shipOff}
          onPress={() => setShip("confirm")} onCancel={() => setShip("idle")}
          onConfirm={() => { setShip("idle"); sampleRun({ total: create, toWire: 2, outcome: "clean" }) }} />
      </div>
    </>
  )
  const progress = run && <div data-block="run"><RunProgress emission={run.e} samples={run.samples} elapsedMs={(run.ended ?? Date.now()) - run.started} /></div>
  return (
    <Frame view="ship" state={state} phone idea="Step 1 cut, step 2 ship, on one page." status={running ? [{ n: "shipping", label: `· ${phaseOf(run!.e).phase === 2 ? "order" : "issues"} ${phaseOf(run!.e).pct}%` }, ...F.SHIP_STATUS] : F.SHIP_STATUS}
      emptyStatus={[{ n: "0", label: "cuts taken" }, { n: "0", label: "issues live on repo" }, { n: "17", label: "units a first cut would record" }]}
      helper={<Helper state={state} title="What this does" explains><p className="m-0">Cut records the build order the spec implies now. Ship creates one issue per new unit on the repo, with blocked-by order. Units already on the repo are skipped; a second press during a run is refused.</p></Helper>}>
      <Target name="cut" state={state} what="the cut preview" handles={["empty", "loading", "stale"]}
        notes={{ empty: { text: "No cut yet. A first cut would record 17 work units in 5 waves.", action: <span className="max-sm:hidden"><Button variant="outline" size="sm" onClick={reload}>Cut work units</Button></span> },
          stale: { text: "4 units changed since cut 3 · Re-cut before shipping.", action: <span className="max-sm:hidden"><Button variant="outline" size="sm" onClick={reload}>Re-cut work units</Button></span> } }}>
        <StepPanel title="1 · Cut" side={<>
          <span data-value=""><b>{cutDone ? "cut 4 taken" : "2 new, 1 gone, 1 split"}</b>{cutDone ? "" : ` = ${F.CHANGED}`}</span>
          <span data-value="" className="text-muted-foreground">last cut: {cutDone ? "cut 4, just now" : "cut 3, 21 Sep 14:10"}</span>
          <span data-value="" className="text-muted-foreground">gates sound · then step 2</span>
          <div className="max-sm:hidden"><RunButton kind="cut" phase={cut} disabledReason={off ?? (state === "loading" ? "loading" : undefined)} onPress={() => { setCut("pending"); setTimeout(() => { setCut("idle"); setCutDone(true) }, 800) }} /></div>
        </>}>
          <DataTable caption="Changes since cut 3" head={["Change since cut 3", "Unit", "Wave"]} rows={cutDone ? [] : F.CUT_DIFF.map(r => [...r])} />
        </StepPanel>
      </Target>
      <Target name="ship" state={state} what="the ship" handles={["error", "partial", "permission", "offline"]}
        notes={{ error: { text: <>the ship failed in phase 1 (creating issues); 2 issues were created before it.</>, action: <span className="max-sm:hidden"><Button variant="outline" size="sm" onClick={reload}>Retry the rest</Button></span> },
          partial: { text: "The last ship stopped part way: 2 of 3 issues created, 1 not created. Take the new cut; Ship then creates it with the rest." },
          permission: { text: "No GitHub token set: Ship to GitHub is off until a token is added to the unit's settings." },
          offline: { text: <>Settings and history from {LAST_GOOD}, not current. Cut and Ship are off while offline.</> } }}
        errorKeeps={<StepPanel title="2 · Ship to GitHub" side={shipSide} below={progress}>{settings}</StepPanel>}>
        <StepPanel title="2 · Ship to GitHub" side={shipSide} below={progress}>{settings}</StepPanel>
      </Target>
      <section data-block="history" className="flex flex-col gap-2">
        <Surface>
        <H2>History</H2>
        <Plain state={state} empty="No cuts or ships yet.">
          <div className="flex flex-col gap-4 lg:flex-row">
            <div className="flex min-w-0 flex-1 flex-col gap-1"><h3 className="m-0 text-sm font-semibold">Cuts</h3>
              <DataTable caption="Cuts" head={["Cut", "Taken", "Units"]} num={[2]} rows={F.CUTS.map(([c, t, u]) => [c, t, u])} /></div>
            <div className="flex min-w-0 flex-1 flex-col gap-1"><h3 className="m-0 text-sm font-semibold">Ships</h3>
              <DataTable caption="Ships" head={["Run", "Cut", "Created", "Held", "Failed"]} num={[2, 3, 4]} rows={F.SHIPS.map(s => [s.run, s.cut, ...(s.counts ?? ["—", "—", "—"])])} />
              <p className="m-0 text-xs text-muted-foreground">— no counts recorded for that run</p></div>
          </div>
        </Plain>
        </Surface>
      </section>
    </Frame>
  )
}

export const screens = { spine: Spine, order: Order, read: Read, waves: Waves, modules: Modules, queues: Queues, ship: Ship }
