import { useEffect, useMemo, useState, type ReactNode } from "react"
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
import { SpineGraph, chainOf, type EdgeKind } from "@/components/ui/spine-graph"
import { FileTree, countFiles, flags, type FileRow, type Folder } from "@/components/ui/file-tree"
import { WaveTimeline } from "@/components/ui/wave-timeline"
import { RunProgress } from "@/components/ui/run-progress"
import { phaseOf, useRun, watchRun } from "@/lib/run"
import { entry, getStore, refresh, reviewLayer, selectProject, useStore, watchEmission, write, type Posted, type ProjectData, type Reviewed } from "@/lib/api"
import * as M from "@/lib/model"

// The seven views of the approved design, drawn from the unit's own routes (lib/api). Block names match the
// wireframes. A view's state (empty, loading, error, stale, partial, offline) comes from the real reads, in the block
// it affects. Every string the unit stored (titles, bodies, claims, reasons) is rendered as React text, never markup.

export const VIEWS: [string, string][] = [["spine", "Spine"], ["order", "Order"], ["read", "Read"], ["waves", "Waves"], ["modules", "Modules"], ["queues", "Queues"], ["ship", "Ship"]]

/** One hash query value, re-read on every hash change. */
function useQuery(key: string): string | null {
  const read = () => new URLSearchParams(location.hash.split("?")[1] ?? "").get(key)
  const [v, setV] = useState(read)
  useEffect(() => { const on = () => setV(read()); addEventListener("hashchange", on); return () => removeEventListener("hashchange", on) }, [key])
  return v
}
const go = (h: string) => { location.hash = h }
const reload = () => { void refresh() }
const link = "rounded-sm text-foreground underline underline-offset-4 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
const clock = (ms: number | null) => (ms ? new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "never")
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
/** The project's data, or an empty stand-in so a view can render its frame before anything arrives. */
const EMPTY: ProjectData = {
  project: "", spine: [], gates: null, waves: [], slices: [], issues: [], requests: [], modules: { modules: [], untested: [], unimplemented_tests: [], uncovered_tests: [] },
  proposal: null, units: { cut: null, drift: null, live: null }, cuts: [], repo: null, labels: [], pickup: [], emission: { running: false, phase: "done" }, delegation: false, ships: [],
}
function useData() { const s = useStore(); return { s, d: s.data ?? EMPTY, p: s.project ?? "" } }

/** One search across every view: an entry id (S-04, S·04, s4) focuses it on Spine; anything else opens Read filtered. */
function search(q: string) {
  const m = /^([IBASTM])\s*[-·.]?\s*(\d{1,4})$/i.exec(q), d = getStore().data
  const hit = m && d?.spine.find(e => e.id[0] === m[1].toUpperCase() && Number(e.id.slice(2)) === Number(m[2]))
  go(hit ? `#/spine?entry=${encodeURIComponent(hit.id)}` : `#/read?q=${encodeURIComponent(q)}`)
}

// ---------- frame ----------
function Frame({ view, state, idea, phone = false, rail, status, emptyStatus, helper, children }: {
  view: string; state: ScreenState; idea: string; phone?: boolean; rail?: ReactNode; status?: StatusItem[]; emptyStatus?: StatusItem[]; helper?: ReactNode; children: ReactNode
}) {
  const { s, d, p } = useData()
  const name = VIEWS.find(([v]) => v === view)![1]
  const run = useRun()
  const slices = useMemo(() => M.slicesOf(d), [d])
  const noProjects = !!s.projects && !s.projects.length
  return (
    <main data-screen={view} className="mx-auto flex max-w-[1440px] flex-col gap-4 px-4 py-3 sm:px-6 sm:py-4">
      <div className="flex flex-wrap items-center gap-4">
        <div data-block="project">
          {noProjects ? <p className="m-0 text-sm text-muted-foreground">No projects yet: a project appears once a request initiates one.</p>
            : <ProjectPicker projects={s.projects ?? (p ? [p] : [])} project={p} forks={[]} fork="" disabledReason={s.projects ? undefined : "loading projects"} onProject={selectProject} />}
        </div>
        <div data-block="search" className="min-w-0 flex-1 max-sm:order-first max-sm:basis-full"><SearchBox onSearch={search} disabledReason={s.data ? undefined : "nothing loaded yet"} /></div>
        <div data-block="freshness"><Freshness updated={clock(s.updated)} variant={state === "stale" ? "stale" : state === "offline" ? "offline" : "default"} onRefresh={reload} /></div>
      </div>
      <h1 className="m-0 text-xl font-semibold text-foreground">{name}{p ? ` · ${p}` : ""}</h1>
      <p data-block="idea" className="m-0 text-sm text-muted-foreground">{idea}</p>
      {phone && <div data-block="phone-note" className="sm:hidden"><PhoneNote /></div>}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <div className="flex flex-col gap-4 sm:w-[220px] sm:flex-none">
          <div data-block="views"><ViewNav views={VIEWS} current={view} shipping={run?.e.running ? phaseOf(run.e).pct : undefined} phase={run?.e.running && phaseOf(run.e).phase === 2 ? "order" : "issues"} /></div>
          {rail ?? <div data-block="slices" className="max-sm:hidden">
            {slices.length ? <SliceList title="Slices" slices={slices} /> : <p className="m-0 text-sm text-muted-foreground">No slices yet.</p>}
          </div>}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {status && <div data-block="status"><StatusLine items={state === "empty" || state === "error" ? emptyStatus ?? zeroed(status) : status} loading={state === "loading"} /></div>}
          {children}
        </div>
        {helper}
      </div>
    </main>
  )
}
/** Empty: counts read 0, or a dash where a word stood. */
const zeroed = (items: StatusItem[]): StatusItem[] => items.map(x => ({ n: /^\d/.test(x.n) ? "0" : "—", label: x.label }))

/** The helper panel: nothing selected when the view is empty; values greyed while loading. `explains`: the panel says
 *  what the view does, so it stays in every state. */
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
  const s = useStore()
  const st = state !== "default" && state !== "running" && handles.includes(state) ? state : null
  const at = clock(s.updated)
  const DEFAULTS: Record<BlockState, { text: ReactNode; action?: ReactNode }> = {
    empty: { text: `No ${what} yet.` },
    loading: { text: `Loading ${what}; labels stay, values fill in as they arrive.` },
    error: { text: <>{what} could not be loaded: {s.error ?? "the unit did not answer"}.</>, action: <Button variant="outline" size="sm" onClick={reload}>Retry</Button> },
    stale: { text: <>Showing {what} from {at}; the last refresh failed ({s.error}).</>, action: <Button variant="outline" size="sm" onClick={reload}>Refresh</Button> },
    partial: { text: <>Some of the {what} could not be read: {s.failed.join(", ")}. What loaded is shown.</> },
    offline: { text: <>The unit cannot be reached. Showing {what} from {at}, not current.</> },
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
  if (state === "empty" || state === "error") return <p className="m-0 text-sm text-muted-foreground">{empty}</p>
  return <LoadingVeil on={state === "loading"}>{children}</LoadingVeil>
}

function H2({ children }: { children: ReactNode }) { return <h2 className="m-0 text-base font-semibold text-foreground">{children}</h2> }
/** A panel surface: lists, history and the reading column sit on it, apart from the page background. */
function Surface({ children, className, ...rest }: React.ComponentProps<"div">) {
  return <div className={cn("flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 text-card-foreground", className)} {...rest}>{children}</div>
}
/** An entry id in the mono face. */
function Id({ children }: { children: ReactNode }) { return <span className="font-mono">{children}</span> }
/** A status word with the node's meaning as a small mark beside it; the word stays foreground. */
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
/** The outcome of the last press, in the block it belongs to. Text only. */
function Flash({ flash }: { flash: { ok: boolean; text: string } | null }) {
  if (!flash) return null
  return <p role={flash.ok ? "status" : "alert"} className="m-0 text-sm whitespace-pre-line text-foreground"><Tone tone={flash.ok ? "ok" : "down"}>{flash.ok ? "Done" : "Refused"}</Tone> · {flash.text}</p>
}
type Flashed = { ok: boolean; text: string } | null

/** One entry's body, pulled by id when a panel opens it. */
function useBody(project: string, id: string | undefined) {
  const [body, setBody] = useState<{ id: string; text: string | null } | null>(null)
  useEffect(() => {
    if (!id || !project || M.layerOf(id) === "M") { setBody(null); return }
    let live = true
    setBody(null)
    void entry(project, id).then(e => { if (live) setBody({ id, text: e ? e.body ?? "" : null }) })
    return () => { live = false }
  }, [project, id])
  return body && body.id === id ? body.text : undefined
}
function Body({ text }: { text: string | null | undefined }) {
  if (text === undefined) return <p className="m-0 text-muted-foreground">Loading the body…</p>
  if (text === null) return <p className="m-0 text-muted-foreground">The body could not be loaded.</p>
  return text.trim() ? <p className="m-0 whitespace-pre-wrap break-words">{text}</p> : <p className="m-0 text-muted-foreground">no body yet</p>
}

// ---------- Spine ----------
const EDGE_KINDS: [EdgeKind, string][] = [["derives", "derives from"], ["depends", "depends on"]]

function Spine() {
  const { d, p } = useData()
  const graphData = useMemo(() => M.spineData(d), [d])
  const state = useScreenState(!graphData.nodes.length)
  const entryQ = useQuery("entry")
  const focus = entryQ && graphData.nodes.some(([id]) => id === entryQ) ? entryQ : undefined
  const [edges, setEdges] = useState<string[]>(["derives"]), [test, setTest] = useState<string>()
  useEffect(() => { setTest(undefined) }, [focus])
  const titles = useMemo(() => new Map(d.spine.map(e => [e.id, e.title] as const)), [d])
  const sliceOf = useMemo(() => new Map(graphData.nodes.map(([id, , s]) => [id, s])), [graphData])
  const testsOf = useMemo(() => M.testsBySpec(d), [d])
  const allTests = d.spine.filter(e => M.isTest(e.id))
  const missing = useMemo(() => Object.fromEntries(graphData.nodes.filter(([id, , s]) => !s && M.layerOf(id) !== "I").map(([id]) => [id, "slice: none"])), [graphData])
  const linked = focus ? chainOf(focus, graphData.edges) : new Set<string>()
  const down = focus ? M.downFrom(focus, graphData.edges) : new Set<string>()
  const tests = [...linked].filter(id => M.layerOf(id) === "S").flatMap(s => (testsOf.get(s) ?? []).map(t => ({ ...t, verifies: s })))
  const byLayer = M.GRAPH_LAYERS.map(([k, n]) => [n, [...linked].filter(id => M.layerOf(id) === k)] as const).filter(([, ids]) => ids.length)
  const linkedText = byLayer.map(([n, ids]) => `${ids.length} ${n}`).join(", ") || "nothing"
  const downSpec = focus ? [...down, focus].filter(id => M.layerOf(id) === "S") : [], downFiles = [...down].filter(id => M.layerOf(id) === "M")
  const blastTests = downSpec.reduce((n, s) => n + (testsOf.get(s)?.length ?? 0), 0)
  const body = useBody(p, focus), testBody = useBody(p, test)
  const sel = tests.find(t => t.id === test)
  const layerText = (id: string) => `${M.LAYER_NAME[M.layerOf(id)]} · slice ${sliceOf.get(id) ?? (M.layerOf(id) === "I" ? "none (intent is not sliced)" : "none")}`
  const modRow = focus && M.layerOf(focus) === "M" ? d.modules.modules.find(m => m.path === focus) : undefined
  const counts = M.countBy(d.spine, e => M.layerOf(e.id))
  const pending = d.requests.filter(r => r.status === "pending").length
  const moved = d.units.drift?.changed.length ?? 0
  const status: StatusItem[] = [
    ...M.GRAPH_LAYERS.slice(0, 4).map(([k, n]) => ({ n: String(counts.get(k) ?? 0), label: n })),
    { n: String(allTests.length), label: `tests · ${plural(d.modules.untested.length, "spec entry", "spec entries")} untested` },
    d.gates ? { n: d.gates.sound ? "sound" : "unsound", label: "graph", tone: d.gates.sound ? "ok" as const : "down" as const } : { n: "—", label: "graph: gates not read" },
    ...(d.units.cut ? [{ n: String(moved), label: `units changed since cut ${d.units.cut.seq}`, tone: moved ? "warn" as const : undefined, link: moved ? "cut and ship" : undefined, href: "#/ship" }] : [{ n: "0", label: "cuts taken", link: "cut and ship", href: "#/ship" }]),
    { n: String(pending), label: pending === 1 ? "request waiting" : "requests waiting", tone: pending ? "warn" as const : undefined, link: pending ? "open" : undefined, href: "#/queues?list=requests" },
  ]
  // How far each slice has reached: entries per layer, and how many of its spec entries a test verifies.
  const specState = M.slicesOf(d).map(([s]) => {
    const inSlice = d.spine.filter(e => e.slice === s), n = (k: string) => inSlice.filter(e => M.layerOf(e.id) === k).length
    const spec = inSlice.filter(e => M.layerOf(e.id) === "S"), tested = spec.filter(e => testsOf.has(e.id)).length
    return [s, n("B"), n("A"), spec.length, spec.length ? `${tested} of ${spec.length}` : "none"]
  })
  const graph = (
    <div className="flex flex-col gap-3 max-sm:hidden">
      <SpineGraph data={graphData} focus={focus} edges={edges as EdgeKind[]} missing={missing} height={640} hrefFor={id => `#/spine?entry=${encodeURIComponent(id)}`} sliceHref={s => `#/read?slice=${encodeURIComponent(s)}`}
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
          : <p className="m-0 text-sm text-muted-foreground">{focus ? `No test verifies the spec entries linked to ${focus}.` : "Pick an entry on the graph to list the tests of its chain."}</p>}
      </div>
    </div>
  )
  // Phone is information only: a summary of the focus, then its chain as a list by layer.
  const chain = (
    <div data-part="chain" className="flex flex-col gap-2 sm:hidden">
      {focus ? <>
        <Surface>
          <H2><Id>{focus}</Id> · {titles.get(focus) ?? graphData.nodes.find(n => n[0] === focus)?.[1] ?? "entry"}</H2>
          <KeyValue rows={[["layer", layerText(focus)], ["linked", linkedText], ["blast radius", `${downSpec.length} spec entries, ${downFiles.length} files, ${blastTests} tests`]]} />
        </Surface>
        <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
          {byLayer.map(([n, ids]) => (
            <li key={n} className="flex flex-col gap-0.5 rounded-lg border border-border bg-card px-3 py-2">
              <span className="text-xs text-muted-foreground">{n} · {ids.length}</span>
              {ids.map(id => <span key={id} className={cn("break-words", id === focus && "font-semibold")}><Id>{id}</Id> · {titles.get(id) ?? "module"}</span>)}
            </li>
          ))}
        </ul>
      </> : <p className="m-0 text-sm text-muted-foreground">{graphData.nodes.length} entries and files. Search an id to see its chain.</p>}
    </div>
  )
  return (
    <Frame view="spine" state={state} idea="Move around the graph and zoom in for names; search or pick an entry to focus its chain." status={status}
      emptyStatus={[...M.GRAPH_LAYERS.slice(0, 4).map(([, n]) => ({ n: "0", label: n })), { n: "0", label: "tests" }, { n: "0", label: "cuts taken" }]}
      helper={
        <Helper state={state} swapKey={focus ?? ""} title={focus ? <><Id>{focus}</Id> · {titles.get(focus) ?? "module"}</> : "Nothing selected"}>
          {focus ? <div className="flex flex-col gap-3">
            <KeyValue rows={[["layer", layerText(focus)], ["linked", linkedText], ["blast radius", `${downSpec.length} spec entries, ${downFiles.length} files, ${blastTests} tests`]]} />
            {modRow ? <>
              <h3 className="m-0 text-sm font-semibold">Implements</h3>
              <p className="m-0"><Id>{modRow.implements.join(", ") || "nothing"}</Id></p>
              <a className={cn(link, "self-start")} href={`#/modules?file=${encodeURIComponent(focus)}`}>Open in Modules</a>
            </> : <>
              <h3 className="m-0 text-sm font-semibold">Body</h3>
              <Body text={body} />
              <a className={cn(link, "self-start")} href={`#/read?q=${encodeURIComponent(focus)}&layer=${M.layerOf(focus)}`}>Open in Read</a>
            </>}
            {sel ? <div className="flex flex-col gap-1"><h3 className="m-0 text-sm font-semibold"><Id>{sel.id}</Id> · {sel.title}</h3><Body text={testBody} /><span className="text-xs text-muted-foreground">verifies <Id>{sel.verifies}</Id></span></div>
              : <p className="m-0 text-muted-foreground max-sm:hidden">Click a test under the graph to inspect it here.</p>}
          </div> : <p className="m-0 text-muted-foreground">Click an entry on the graph, or search its id, to see its chain, body and blast radius here.</p>}
        </Helper>
      }>
      <Target name="graph" state={state} what="spine entries"
        notes={{ empty: { text: `The spec has no entries yet. Layers: ${M.GRAPH_LAYERS.slice(0, 4).map(([, n]) => n).join(", ")}, and tests beside them.`, action: <a className={link} href="#/queues">Open Queues</a> },
          partial: { text: <>Entries with no slice are marked "slice: none". {getStore().failed.length ? `Not read: ${getStore().failed.join(", ")}.` : ""}</> } }}>
        {graph}
        {chain}
      </Target>
      <section data-block="state" className="flex flex-col gap-2">
        <H2>Spec state · how far each slice has reached</H2>
        <Plain state={state} empty="No slices yet: nothing has been drafted.">
          {specState.length ? <DataTable caption="Spec state per slice" head={["Slice", "B", "A", "S", "S tested"]} num={[1, 2, 3]} rows={specState} />
            : <p className="m-0 text-sm text-muted-foreground">No slices yet: entries exist but none is sliced.</p>}
        </Plain>
      </section>
    </Frame>
  )
}

// ---------- Order ----------
function Order() {
  const { s, d, p } = useData()
  const src = M.unitSource(d), units = src?.units ?? []
  const state = useScreenState(!units.length)
  const byKey = useMemo(() => new Map(units.map(u => [u.key, u])), [units])
  const [picked, setUnit] = useState<string>()
  const unit = picked && byKey.has(picked) ? picked : units[0]?.key
  const u = unit ? byKey.get(unit) : undefined
  const waveOf = new Map((src?.waves ?? []).flatMap(w => w.units.map(k => [k, w.wave] as const)))
  const waitsOn = unit ? src?.edges[unit] ?? [] : []
  const testsOf = useMemo(() => M.testsBySpec(d), [d])
  const titles = useMemo(() => new Map(d.spine.map(e => [e.id, e.title] as const)), [d])
  const mods = useMemo(() => new Map(d.modules.modules.map(m => [m.path, m] as const)), [d])
  const unitTests = u ? u.tests ? u.entries.map(id => ({ id, title: titles.get(id) ?? "" })) : u.entries.flatMap(id => testsOf.get(id) ?? []) : []
  const body = useBody(p, u?.anchor)
  const [cut, setCut] = useState<RunPhase>("idle"), [flash, setFlash] = useState<Flashed>(null)
  useEffect(() => setFlash(null), [p])
  const noModules = !d.modules.modules.length
  const recutOff = state === "offline" ? "offline" : state === "error" ? "units failed to load" : state === "loading" ? "loading" : noModules ? "no modules declared" : undefined
  const moved = d.units.drift?.changed.length ?? 0
  const doCut = async () => {
    setCut("pending")
    const r = await write.cut(p)
    setCut("idle")
    if (r.ok) await refresh()
    setFlash(r.ok ? { ok: true, text: cutSaid(r) } : { ok: false, text: r.message })
  }
  return (
    <Frame view="order" state={state} phone idea="Work units on the wave timeline; cut them here."
      status={[{ n: String(units.length), label: d.units.cut ? `units in cut ${d.units.cut.seq}` : "units, live (no cut taken)" }, ...(d.units.cut ? [{ n: String(moved), label: `units changed since cut ${d.units.cut.seq}`, tone: moved ? "warn" as const : undefined }] : []),
        { n: String(src?.waves.length ?? 0), label: "waves" }, { n: String(M.sharedFiles(src)), label: "shared files" }]}
      emptyStatus={[{ n: "0", label: "work units" }, { n: "0", label: "waves" }, { n: "0", label: "shared files" }]}
      helper={
        <Helper state={state} swapKey={unit} title={<Id>{M.unitLabel(u, unit ?? "")}</Id>}>
          {u && <div className="flex flex-col gap-4">
            <KeyValue rows={[["wave", String(waveOf.get(u.key) ?? "—")], ["waits on", waitsOn.length ? <Id>{waitsOn.map(k => M.unitLabel(byKey.get(k), k)).join(", ")}</Id> : "nothing: buildable now"],
              ["slices", u.slices.join(", ") || "none"], ["write set", <Id>{u.modules.join(", ") || "none"}</Id>]]} />
            <div className="flex flex-col gap-1">
              <h3 className="m-0 text-sm font-semibold">{u.tests ? "Tests" : "Implements"}</h3>
              <p className="m-0"><Id>{u.anchor}</Id> · {titles.get(u.anchor) ?? "spec entry"}</p>
              {u.entries.length > 1 && <p className="m-0 text-muted-foreground">with <Id>{u.entries.filter(e => e !== u.anchor).join(", ")}</Id></p>}
              <Body text={body} />
              <a className={cn(link, "self-start")} href={`#/read?q=${encodeURIComponent(u.anchor)}&layer=S`}>Open {u.anchor} in Read</a>
            </div>
            <div className="flex flex-col gap-1">
              <h3 className="m-0 text-sm font-semibold">{u.modules.length === 1 ? "Module" : "Modules"}</h3>
              {u.modules.length ? <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {u.modules.map(f => { const m = mods.get(f), spec = m ? m.implements.filter(i => !M.isTest(i)).length : 0, t = m ? m.implements.filter(M.isTest).length : 0
                  return <li key={f}><span className="font-mono">{f}</span> <span className="text-muted-foreground tabular-nums">· {m ? `${spec} spec · ${t} ${t === 1 ? "test" : "tests"}` : "not declared"}</span></li> })}
              </ul> : <p className="m-0 text-muted-foreground">No files: nothing can be handed out for this unit.</p>}
              {u.modules[0] && <a className={cn(link, "self-start")} href={`#/modules?file=${encodeURIComponent(u.modules[0])}`}>Open in Modules</a>}
            </div>
            {!u.tests && <div className="flex flex-col gap-1">
              <h3 className="m-0 text-sm font-semibold">Tests · {unitTests.length}</h3>
              {unitTests.length ? <ul className="m-0 flex list-none flex-col gap-1 p-0">{unitTests.map(t => <li key={t.id}><Id>{t.id}</Id> · {t.title}</li>)}</ul>
                : <p className="m-0 text-muted-foreground">No test entries verify {u.anchor} yet.</p>}
            </div>}
          </div>}
        </Helper>
      }>
      <div data-block="cut" className="flex flex-col gap-2 text-sm text-foreground">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="flex flex-col gap-1 max-sm:hidden">
            <Button variant="outline" disabled={!!recutOff || cut === "pending"} aria-busy={cut === "pending" || undefined} onClick={doCut}>
              {cut === "pending" ? "Cutting…" : d.units.cut ? "Re-cut work units" : "Cut work units"}{recutOff ? ` · ${recutOff === "offline" ? "offline" : "off"}` : ""}
            </Button>
          </span>
          {recutOff && recutOff !== "offline" && recutOff !== "loading" && <span className="text-muted-foreground max-sm:hidden">Cutting is off: {recutOff}.</span>}
          {d.units.cut ? <><span data-value="">{moved ? `${plural(moved, "unit")} changed since cut ${d.units.cut.seq}` : `matches the graph`}</span><span className="text-muted-foreground">last cut: cut {d.units.cut.seq}, {M.when(d.units.cut.at)}</span></>
            : units.length ? <span className="text-muted-foreground">No cut taken: showing the live projection. Cutting is a person's decision; nothing here has been handed out.</span>
            : <span>No cut yet: no modules are declared.</span>}
        </div>
        <Flash flash={flash} />
      </div>
      <Target name="waves" state={state} what="work units"
        notes={{ empty: { text: "No work units yet: a work unit is one spec entry and the files implementing it, so they appear once modules are declared.", action: <a className={link} href="#/modules">Open Modules</a> },
          partial: { text: <>A unit with no test entries says so in its panel. Not read: {s.failed.join(", ")}.</> } }}>
        <WaveTimeline variant="units" selected={unit} onPick={setUnit}
          waves={(src?.waves ?? []).map(w => ({ title: `Wave ${w.wave} · ${plural(w.units.length, "unit")}`, items: w.units.map(k => {
            const after = src?.edges[k] ?? []
            return { id: k, label: M.unitLabel(byKey.get(k), k), sub: `waits on ${after.length ? plural(after.length, "unit") : "nothing"}${(src?.overlap?.[k] ?? []).length ? " · shares files" : ""}` }
          }) }))} />
      </Target>
    </Frame>
  )
}
function cutSaid(r: Posted): string {
  const x = r.payload ?? {}, moved = x.drift?.changed?.length ?? 0, un = x.unimplemented?.length ?? 0
  return `Cut ${x.seq} taken: ${plural(x.units?.length ?? 0, "work unit")}` + (un ? `, and ${plural(un, "spec entry is", "spec entries are")} in none of them` : "")
    + (x.seq > 1 ? (moved ? `. ${plural(moved, "unit")} moved since the last cut.` : ". Nothing moved since the last cut.") : ".")
}

// ---------- Read ----------
const READ_LAYERS = M.LAYERS.filter(([k]) => k !== "M")
let readToken = 0
function Read() {
  const { s, d, p } = useData()
  const q0 = useQuery("q") ?? "", slice = useQuery("slice"), layerQ = useQuery("layer")
  const [q, setQ] = useState(q0), [layer, setLayer] = useState(layerQ && READ_LAYERS.some(([k]) => k === layerQ) ? layerQ : "B")
  const [rows, setRows] = useState<{ key: string; entries: Reviewed[] | null; failed?: string }>({ key: "", entries: null })
  useEffect(() => setQ(q0), [q0])
  useEffect(() => { if (layerQ && READ_LAYERS.some(([k]) => k === layerQ)) setLayer(layerQ) }, [layerQ])
  // One request per layer, bodies included. Re-read when the graph changes; the slower of two loads in flight never
  // overwrites the newer one.
  const key = `${p}|${layer}`
  useEffect(() => {
    if (!p || !s.data) return
    const mine = ++readToken
    void reviewLayer(p, layer).then(entries => { if (mine === readToken) setRows({ key, entries }) }, e => { if (mine === readToken) setRows(r => ({ key, entries: r.key === key ? r.entries : null, failed: String(e.message ?? e) })) })
  }, [key, d])
  const loaded = rows.key === key ? rows.entries : null
  const empty = !!loaded && !loaded.length
  const base = useScreenState(empty)
  const state: ScreenState = base === "default" && rows.key === key && rows.failed ? "stale" : base === "default" && !loaded ? "loading" : base
  useEffect(() => { if (slice && loaded) document.getElementById(`slice-${slice}`)?.scrollIntoView() }, [slice, loaded])
  const needle = q.trim().toLowerCase()
  const groups = useMemo(() => {
    const m = new Map<string, Reviewed[]>()
    for (const e of loaded ?? []) { const g = e.slice ?? (layer === "I" ? "intent" : "no slice"); (m.get(g) ?? m.set(g, []).get(g)!).push(e) }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([g, es]) => ({ slice: g, total: es.length, entries: es.filter(e => !needle || `${e.id} ${e.title} ${e.body}`.toLowerCase().includes(needle)) }))
  }, [loaded, needle, layer])
  const outline: [string, number][] = groups.map(g => [g.slice, g.total])
  const LN = M.LAYER_NAME
  return (
    <Frame view="read" state={state} idea="The corpus, entry by entry, at full width; every entry stays reachable."
      rail={<div data-block="outline" className="max-sm:hidden">{outline.length ? <SliceList title={`Outline · ${LN[layer]}`} slices={outline} onPick={g => go(`#/read?slice=${encodeURIComponent(g)}&layer=${layer}`)} /> : <p className="m-0 text-sm text-muted-foreground">No slices in this layer yet.</p>}</div>}>
      <div data-block="filter" className="flex flex-wrap items-center gap-3">
        <Input type="search" aria-label="Find in corpus" placeholder="Find S-04 or a word" value={q} onChange={e => setQ(e.target.value)} className="w-[280px] max-w-full" />
        <ToggleGroup type="single" variant="outline" size="sm" value={layer} onValueChange={v => v && setLayer(v)} aria-label="Layer">
          {READ_LAYERS.map(([k, n]) => <ToggleGroupItem key={k} value={k} aria-label={n}>{k}</ToggleGroupItem>)}
        </ToggleGroup>
        <span className="text-sm text-muted-foreground">{loaded ? loaded.length : "…"} entries · {LN[layer]}</span>
      </div>
      <Target name="entries" state={state} what="entries"
        notes={{ empty: { text: `No entries in the ${LN[layer]} layer yet.`, action: <Button variant="outline" size="sm" onClick={() => setLayer(layer === "B" ? "I" : "B")}>Show {layer === "B" ? "intent" : "behaviour"}</Button> },
          stale: rows.failed ? { text: <>The {LN[layer]} layer could not be re-read ({rows.failed}); {loaded ? "what loaded before stays readable." : "nothing to show yet."}</>, action: <Button variant="outline" size="sm" onClick={reload}>Retry</Button> } : undefined,
          loading: { text: `Loading the ${LN[layer]} layer; a slow load never reads as "no entries".` } }}>
        {groups.map(g => (
          <section key={g.slice} id={`slice-${g.slice}`} className="flex scroll-mt-4 flex-col gap-3">
            <H2>{g.slice} · {plural(g.total, `${LN[layer]} entry`, `${LN[layer]} entries`)}</H2>
            {g.entries.map(e => <EntryArticle key={e.id} id={e.id} title={e.title} body={e.body || undefined} meta={`${e.derives_from.length ? `derives from ${e.derives_from.join(", ")}` : "derives from nothing"} · slice ${e.slice ?? "none"}`} />)}
            {!g.entries.length && <p className="m-0 py-2 text-sm text-muted-foreground">No entry in {g.slice} matches "{q}".</p>}
          </section>
        ))}
      </Target>
    </Frame>
  )
}

// ---------- Waves ----------
function Waves() {
  const { s, d } = useData()
  const state = useScreenState(!d.waves.length)
  const info = new Map(d.slices.map(x => [x.slice, x]))
  const insightsMissing = s.failed.some(f => f.endsWith("/insights"))
  return (
    <Frame view="waves" state={state} idea="Slices by the wave they can be written in."
      helper={<Helper state={state} title="About Waves" explains><p className="m-0">The writing order, from slice dependencies the unit projects from entry edges. Build order is in <a className={link} href="#/order">Order</a>.</p></Helper>}>
      <Target name="waves" state={state} what="waves" notes={{ empty: { text: "No slices yet, so there are no writing waves.", action: <a className={link} href="#/spine">Open Spine</a> },
        partial: { text: insightsMissing ? "Slice sizes and cohesion could not be read; each card marks them pending." : `Not read: ${s.failed.join(", ")}.` } }}>
        <WaveTimeline variant="slices" waves={d.waves.map(w => ({ title: `Wave ${w.wave}`, items: w.slices.map(name => {
          const x = info.get(name)
          return { id: name, label: name, href: `#/read?slice=${encodeURIComponent(name)}`,
            sub: x ? `${x.size} entries · after ${x.depends_on.join(", ") || "nothing"} · ${x.cohesion == null ? "cohesion n/a" : `cohesion ${x.cohesion.toFixed(2)}`}` : "size and cohesion pending" }
        }) }))} />
      </Target>
    </Frame>
  )
}

// ---------- Modules ----------
const allFiles = (dir: Folder): FileRow[] => [...dir.files, ...dir.folders.flatMap(allFiles)]
function Modules() {
  const { d, p } = useData()
  const tree = useMemo(() => M.treeOf(p, d), [p, d])
  const state = useScreenState(!d.modules.modules.length)
  const fileQ = useQuery("file"), [picked, setFile] = useState<string>()
  useEffect(() => { if (fileQ) setFile(fileQ) }, [fileQ])
  const rows = allFiles(tree)
  const file = picked && d.modules.modules.some(m => m.path === picked) ? picked : d.modules.modules[0]?.path
  const m = d.modules.modules.find(x => x.path === file), row = rows.find(r => r.path === file)
  const spec = m ? m.implements.filter(i => !M.isTest(i)) : [], tests = m ? m.implements.filter(M.isTest) : []
  const specs = d.spine.filter(e => M.layerOf(e.id) === "S").map(e => e.id)
  const claimed = new Set(d.modules.modules.flatMap(x => x.implements))
  return (
    <Frame view="modules" state={state} idea="Fold and unfold folders; click a file to see it on the right."
      status={[{ n: String(rows.length), label: "files" }, { n: `${specs.filter(s => claimed.has(s)).length} / ${specs.length}`, label: "spec entries claimed" },
        { n: String(d.modules.untested.length), label: "spec entries with no test" }, { n: String(rows.filter(r => r.slices > 1).length), label: "files serving several slices" }]}
      helper={
        <Helper state={state} swapKey={file} title={file ?? "Nothing selected"}>
          {m && row && <div className="flex flex-col gap-2">
            <KeyValue rows={[["slices", m.slices.join(", ") || "none"], ["spec entries", spec.length ? <span className="flex flex-wrap gap-x-2">{spec.map(s => <a key={s} className={cn(link, "font-mono")} href={`#/spine?entry=${encodeURIComponent(s)}`}>{s}</a>)}</span> : "none"],
              ["test entries", tests.length ? <Id>{tests.join(" ")}</Id> : "none"], ["covered by", m.covered_by.length ? <Id>{m.covered_by.join(", ")}</Id> : "none"],
              ["flags", flags(row).length ? <span className="flex flex-wrap gap-1">{flags(row).map(x => <Badge key={x} variant="outline">{x}</Badge>)}</span> : "none"]]} />
          </div>}
        </Helper>
      }>
      <Target name="tree" state={state} what="files" notes={{ empty: { text: "No files declared yet: a module is declared with the spec entries it implements.", action: <Button variant="outline" size="sm" onClick={reload}>Refresh</Button> } }}>
        <H2>Files · {countFiles(tree)} <span className="font-normal text-muted-foreground">· click a folder to fold it</span></H2>
        <FileTree root={tree} selected={file} onPick={setFile} />
      </Target>
    </Frame>
  )
}

// ---------- Queues ----------
type Sort = "newest" | "undecided" | "id"
const UNDECIDED = ["pending", "open", "escalated"]
const sorted = <T extends { id: string; status: string; at: number }>(xs: T[], s: Sort) => [...xs].sort((a, b) =>
  s === "newest" ? (b.at ?? 0) - (a.at ?? 0) : s === "undecided" ? Number(!UNDECIDED.includes(a.status)) - Number(!UNDECIDED.includes(b.status)) || (b.at ?? 0) - (a.at ?? 0) : a.id.localeCompare(b.id, undefined, { numeric: true }))
function Queues() {
  const { s, d, p } = useData()
  const state = useScreenState(!d.requests.length && !d.issues.length && !d.proposal)
  const listQ = useQuery("list")
  const [sort, setSort] = useState<Sort>("newest"), [picked, setReq] = useState<string>()
  const list = listQ === "issues" ? "issues" : "requests"
  const reqs = sorted(d.requests, sort), iss = sorted(d.issues, sort)
  const req = picked && d.requests.some(x => x.id === picked) ? picked : reqs[0]?.id
  const r = d.requests.find(x => x.id === req)
  const off = state === "offline"
  const pending = d.requests.filter(x => x.status === "pending").length, open = d.issues.filter(x => UNDECIDED.includes(x.status)).length
  const byStatus = [...M.countBy(d.requests, x => x.status).entries()]
  // The pending slicing decision. The rejection note is required: it is kept as `last_rejection` and shown to the next
  // slicing session, so it does not propose the same cut again. The draft lives in state, so a refresh never eats it.
  const [rejecting, setRejecting] = useState(false), [note, setNote] = useState(""), [busy, setBusy] = useState(false), [flash, setFlash] = useState<Flashed>(null)
  useEffect(() => { setFlash(null); setRejecting(false); setNote("") }, [p])
  const decide = async (what: "ratify" | "reject") => {
    setBusy(true)
    const res = what === "ratify" ? await write.ratify(p) : await write.reject(p, note.trim())
    setBusy(false)
    if (!res.ok) { setFlash({ ok: false, text: res.message }); return }
    setRejecting(false); setNote("")
    await refresh() // re-read first: the decision changes slices, waves and the proposal itself
    setFlash({ ok: true, text: what === "ratify" ? `Ratified. ${plural(Object.keys(res.payload?.slices ?? {}).length, "slice")} now exist.` : "Rejected. The reason is kept and shown to the next slicing session as last_rejection." })
  }
  const prop = d.proposal, score = prop?.score
  const requests = (
    <div className="flex flex-col gap-2">
      <H2>Requests · {pending} pending</H2>
      {reqs.length ? <DataTable caption="Requests" head={["Id", "Type", "Title", "Status", "At"]} rows={reqs.map(x => [
        <Button variant="link" className={cn(link, "h-auto p-0 font-mono font-normal")} aria-pressed={req === x.id} onClick={() => setReq(x.id)}>{x.id}</Button>, x.type, x.title, <Tone tone={x.status === "pending" ? "warn" : undefined}>{x.status}</Tone>, M.when(x.at)])}
        selected={reqs.findIndex(x => x.id === req)} /> : <p className="m-0 text-sm text-muted-foreground">No pending requests: nothing in the queue.</p>}
    </div>
  )
  const issues = (
    <div className="flex flex-col gap-2">
      <H2>Issues · {open} open</H2>
      {iss.length ? <DataTable caption="Issues" head={["Id", "Target", "Slice", "Kind", "Claim", "Status"]} rows={iss.map(x => [<Id>{x.id}</Id>, <a className={cn(link, "font-mono")} href={`#/spine?entry=${encodeURIComponent(x.target)}`}>{x.target}</a>, x.target_slice ?? "none", x.kind, x.claim, <Tone tone={UNDECIDED.includes(x.status) ? "warn" : undefined}>{x.status}</Tone>])} />
        : <p className="m-0 text-sm text-muted-foreground">No open issues: none raised.</p>}
    </div>
  )
  const switcher = (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <ToggleGroup type="single" variant="outline" value={list} onValueChange={v => v && go(`#/queues?list=${v}`)} aria-label="List shown">
        <ToggleGroupItem value="requests">Requests {d.requests.length}</ToggleGroupItem>
        <ToggleGroupItem value="issues">Issues {d.issues.length} · {open} open</ToggleGroupItem>
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
    <Frame view="queues" state={state} phone idea="Jump to Requests or Issues; each list sorts by time, status or id."
      status={[...(byStatus.length ? byStatus.map(([k, n]) => ({ n: String(n), label: k, tone: k === "pending" ? "warn" as const : undefined })) : [{ n: "0", label: "requests" }]),
        { n: String(open), label: "open issues", link: "show", href: "#/queues?list=issues" }, ...(prop ? [{ n: "1", label: "slicing proposal waiting", tone: "warn" as const }] : [])]}
      helper={<Helper state={state} swapKey={r?.id} title={r ? <Id>{r.id}</Id> : "Nothing selected"}>{r ? <div className="flex flex-col gap-2"><KeyValue rows={[["type", r.type], ["status", <Tone tone={r.status === "pending" ? "warn" : undefined}>{r.status}</Tone>], ["received", M.when(r.at)]]} /><p className="m-0 break-words font-semibold">{r.title}</p>{r.body ? <p className="m-0 whitespace-pre-wrap break-words">{r.body}</p> : null}</div> : <p className="m-0 text-muted-foreground">No request to show.</p>}</Helper>}>
      <div data-block="proposal" className="flex flex-col gap-2">
        {!prop ? <p className="m-0 text-sm text-muted-foreground">No slicing proposal is waiting.</p> : (
          <LoadingVeil on={state === "loading"}>
            <Callout actions={<span className="flex gap-2 max-sm:hidden">
              <Button variant="outline" size="sm" disabled={off || busy} onClick={() => decide("ratify")}>{off ? "Ratify · offline" : "Ratify: create these slices"}</Button>
              <Button variant="outline" size="sm" disabled={off || busy} aria-expanded={rejecting} onClick={() => setRejecting(true)}>{off ? "Reject · offline" : "Reject with note"}</Button>
            </span>}>
              <Tone tone="warn"><b>A slicing proposal is waiting on you</b></Tone> · {plural(Object.keys(prop.proposal).length, "slice")}
              {score ? <> · {score.legal === false ? "the gates would refuse this cut" : "passes every gate"} · {plural(score.waves?.length ?? 0, "wave")} · {score.unassigned?.length ?? 0} unassigned</> : " · not scored"}
              <span className="block text-muted-foreground">{Object.entries(prop.proposal).map(([n, ids]) => `${n} (${ids.length})`).join(", ")}. Slices split and never merge, so ratifying is close to one-way.</span>
              {prop.note ? <span className="block whitespace-pre-wrap break-words">{prop.note}</span> : null}
            </Callout>
          </LoadingVeil>
        )}
        {prop && rejecting && <div className="flex flex-col gap-2 max-sm:hidden">
          <Label htmlFor="reject-note">Why is this cut wrong? (required: the next slicing session reads it)</Label>
          <textarea id="reject-note" value={note} onChange={e => setNote(e.target.value)} rows={3} className="min-h-16 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50" />
          <span className="flex gap-2">
            <Button variant="outline" size="sm" disabled={!note.trim() || busy || off} onClick={() => decide("reject")}>Confirm rejection</Button>
            <Button variant="outline" size="sm" onClick={() => { setRejecting(false); setNote("") }}>Cancel</Button>
          </span>
        </div>}
        <Flash flash={flash} />
      </div>
      <Target name="lists" state={state} what="requests and issues"
        notes={{ empty: { text: "No pending requests. No open issues.", action: <Button variant="outline" size="sm" onClick={reload}>Refresh</Button> },
          partial: { text: <>An issue with no target slice shows the slice as none. Not read: {s.failed.join(", ")}.</> } }}>
        <Surface>
          {switcher}
          {list === "issues" ? issues : requests}
        </Surface>
      </Target>
    </Frame>
  )
}

// ---------- Ship ----------
const HORIZONS: [string, string][] = [["w1", "next wave"], ["w2", "next 2 waves"], ["w3", "next 3 waves"], ["w5", "next 5 waves"], ["shipped", "shipped so far"], ["one", "one unit"]]
const split = (v: string) => v.split(",").map(x => x.trim()).filter(Boolean)
function Ship() {
  const { s, d, p } = useData()
  const cutUnits = d.units.cut?.units ?? []
  const state = useScreenState(!d.units.cut && !d.ships.length)
  const run = useRun(), running = !!run?.e.running || !!d.emission.running
  const off = state === "offline" ? "offline" : undefined
  const ready = M.shipReady(d)
  // A run already under way when the page opens is watched from where it is.
  useEffect(() => { if (p && d.emission.running && !run?.e.running) watchRun(watchEmission(p)) }, [p, d.emission.running])
  // Drafts: what the person typed, until saved. A refresh never overwrites them.
  const [repo, setRepo] = useState<string | null>(null), [labels, setLabels] = useState<string | null>(null), [pickup, setPickup] = useState<string | null>(null)
  const [hz, setHz] = useState<string | null>(null), [one, setOne] = useState("")
  const [cut, setCut] = useState<RunPhase>("idle"), [ship, setShip] = useState<RunPhase>("idle")
  const [flash, setFlash] = useState<Flashed>(null), [cutFlash, setCutFlash] = useState<Flashed>(null)
  useEffect(() => { setRepo(null); setLabels(null); setPickup(null); setHz(null); setOne(""); setFlash(null); setCutFlash(null) }, [p])
  const horizonKey = hz ?? (Object.keys(d.emission.already ?? {}).length ? "shipped" : "w1")
  const horizon: M.Horizon = horizonKey === "shipped" ? { mode: "shipped" } : horizonKey === "one" ? { mode: "one", unit: one || cutUnits[0]?.key || "" } : { mode: "more", waves: Number(horizonKey.slice(1)) }
  const pf = M.preflight(d, horizon)
  const shipOff = running ? undefined : off ?? (!ready.token ? "No GitHub token set" : !ready.repo ? "Ship · set a repository first" : !ready.cut ? "Ship · take the first cut" : !ready.sound ? "Ship · the graph is unsound" : !ready.fresh ? "Ship · take the new cut first" : !pf.create ? "Ship · nothing to create" : undefined)
  const said = async (r: Promise<Posted>, ok: (x: any) => string, done?: () => void) => {
    const res = await r
    // Re-read before saying it landed: the next press (pickup sends the labels too) must see what was stored.
    if (res.ok) { done?.(); await refresh() }
    setFlash(res.ok ? { ok: true, text: ok(res.payload ?? {}) } : { ok: false, text: res.message })
  }
  const doCut = async () => {
    setCut("pending")
    const r = await write.cut(p)
    setCut("idle")
    if (r.ok) await refresh()
    setCutFlash(r.ok ? { ok: true, text: cutSaid(r) } : { ok: false, text: r.message })
  }
  const doShip = async () => {
    setShip("pending")
    const body = horizon.mode === "one" ? { unit: horizon.unit } : { waves: horizon.mode === "shipped" ? "shipped" as const : horizon.waves }
    const res = await write.ship(p, d.emission.build ? { ...body, build: d.emission.build } : body)
    setShip("idle")
    if (!res.ok) { setFlash({ ok: false, text: res.message }); return }
    // 202: accepted, not finished. Paced writes take minutes, so what comes back is a run to watch.
    setFlash({ ok: true, text: `Run ${res.payload?.run_id ?? ""} started.` })
    watchRun(watchEmission(p))
  }
  // When a watched run finishes, re-read once: the log and `already` moved.
  useEffect(() => { if (run && !run.e.running) void refresh() }, [run?.e.running])
  const login = d.emission.identity?.login
  const settings = (
    <>
      <div className="grid grid-cols-[max-content_minmax(0,1fr)_max-content] items-center gap-x-3 gap-y-2 text-sm max-sm:hidden">
        <Label htmlFor="ship-repo">Repo</Label><Input id="ship-repo" value={repo ?? d.repo ?? ""} placeholder="owner/name" onChange={e => setRepo(e.target.value)} autoCapitalize="none" autoCorrect="off" />
        <Button variant="outline" size="sm" disabled={repo === null || !!off} onClick={() => said(write.setRepo(p, repo!.trim() || null), x => x.repo ? `Repository set to ${x.repo}.` : "Repository cleared.", () => setRepo(null))}>Save</Button>
        <Label htmlFor="ship-labels">Labels</Label><Input id="ship-labels" value={labels ?? d.labels.join(", ")} placeholder="team:core, area:spec" onChange={e => setLabels(e.target.value)} />
        <Button variant="outline" size="sm" disabled={labels === null || !!off} onClick={() => said(write.setLabels(p, split(labels!)), x => (x.labels ?? []).length ? `Every issue will carry ${x.labels.join(", ")}.` : "No labels beyond the per-unit ones.", () => setLabels(null))}>Save</Button>
        <Label htmlFor="ship-pickup">Pickup labels</Label><Input id="ship-pickup" value={pickup ?? d.pickup.join(", ")} placeholder="labels the consumer adds on pickup" onChange={e => setPickup(e.target.value)} />
        <Button variant="outline" size="sm" disabled={pickup === null || !!off} onClick={() => said(write.setLabels(p, d.labels, split(pickup!)), x => (x.pickup ?? []).length ? `Picked up means labelled ${x.pickup.join(" or ")}.` : "No pickup labels: nothing already out will be edited.", () => setPickup(null))}>Save</Button>
        <Label htmlFor="ship-horizon">Horizon</Label>
        <NativeSelect id="ship-horizon" size="sm" value={horizonKey} onChange={e => setHz(e.target.value)} className="w-full">{HORIZONS.map(([k, n]) => <NativeSelectOption key={k} value={k}>{n}</NativeSelectOption>)}</NativeSelect>
        <span />
        {horizon.mode === "one" && <>
          <Label htmlFor="ship-unit">Unit</Label>
          <NativeSelect id="ship-unit" size="sm" value={horizon.unit} onChange={e => setOne(e.target.value)} className="w-full">{cutUnits.map(u => <NativeSelectOption key={u.key} value={u.key}>{M.unitLabel(u, u.key)}</NativeSelectOption>)}</NativeSelect>
          <span />
        </>}
        <span className="col-span-3 flex items-center gap-2"><Checkbox id="ship-delegate" checked={d.delegation} disabled={!!off} onCheckedChange={v => said(write.setDelegation(p, v === true), x => x.delegation?.enabled ? "Ship delegated: agents may ship S/T repairs of units already out." : "Delegation revoked: only this button ships.")} /><Label htmlFor="ship-delegate">Delegate to agents (S/T repairs of shipped units, every gate green)</Label></span>
      </div>
      <p className="m-0 text-sm text-muted-foreground sm:hidden">{d.repo ?? "no repository"} · {HORIZONS.find(([k]) => k === horizonKey)?.[1]}</p>
      <Flash flash={flash} />
    </>
  )
  const shipSide = (
    <>
      <LoadingVeil on={state === "loading"}><div className="flex flex-col gap-2">
        {!d.units.cut ? <span>Nothing to ship: no cut is taken yet.</span> : <>
          <span>Ships cut {d.units.cut.seq}: <b data-value="">{pf.create}</b> {pf.create === 1 ? "issue" : "issues"} would be created now{horizon.mode === "more" && pf.waves ? ` (${plural(pf.waves, "wave")})` : ""}</span>
          {!ready.fresh && <span className="text-muted-foreground">{plural(ready.moved, "unit")} changed since cut {d.units.cut.seq}: take the new cut first.</span>}
          <span><b data-value="">{pf.already}</b> already out · <b data-value="">{pf.held}</b> held back</span>
        </>}
        <span className="text-muted-foreground">{!ready.token ? "No GitHub token set (MU_SPEC_GITHUB_TOKEN in the unit's environment)" : `token: ${login ?? "unknown"}`} · build {d.emission.build ? d.emission.build.slice(0, 7) : "unknown"}</span>
        {!ready.repo && <span className="text-muted-foreground">No repository set.</span>}
        {d.gates && !d.gates.sound && <span className="text-muted-foreground">The graph is unsound: every unit is refused while that holds.</span>}
      </div></LoadingVeil>
      <div className="max-sm:hidden">
        <RunButton kind="ship" phase={running ? "pending" : ship} count={pf.create} disabledReason={shipOff}
          onPress={() => setShip("confirm")} onCancel={() => setShip("idle")} onConfirm={doShip} />
      </div>
    </>
  )
  const shown = run?.e ?? (d.emission.running ? d.emission : null)
  const progress = shown && <div data-block="run"><RunProgress emission={shown} samples={run?.samples} elapsedMs={run ? (run.ended ?? Date.now()) - run.started : 0} /></div>
  const diff = M.cutDiff(d)
  const ships = d.ships.map(e => [M.when(e.at), e.facts.cut_seq != null ? `cut ${e.facts.cut_seq}` : "—", e.kind === "ship_failed" ? "failed" : "done",
    e.facts.created ?? "—", e.facts.failed ?? "—", e.kind === "ship_failed" ? String(e.facts.error ?? e.facts.reason ?? "") : e.facts.repo ?? ""])
  return (
    <Frame view="ship" state={state} phone idea="Step 1 cut, step 2 ship, on one page."
      status={[...(running && run ? [{ n: "shipping", label: `· ${phaseOf(run.e).phase === 2 ? "order" : "issues"} ${phaseOf(run.e).pct}%` }] : []),
        { n: String(ready.units), label: d.units.cut ? `units in cut ${d.units.cut.seq}` : "units cut" }, { n: String(pf.already), label: "issues live on repo" }, { n: String(pf.create), label: "would create now" },
        { n: String(pf.held), label: "held" }, ...(d.units.cut ? [{ n: String(ready.moved), label: `units changed since cut ${d.units.cut.seq}`, tone: ready.moved ? "warn" as const : undefined }] : [])]}
      emptyStatus={[{ n: "0", label: "cuts taken" }, { n: "0", label: "issues live on repo" }, { n: String(d.units.live?.units.length ?? 0), label: "units a first cut would record" }]}
      helper={<Helper state={state} title="What this does" explains><p className="m-0">Cut records the build order the spec implies now. Ship creates one issue per new unit on the repo, with blocked-by order. Units already on the repo are skipped; a second press during a run is refused.</p></Helper>}>
      <Target name="cut" state={state} what="the cut preview" handles={["empty", "loading", "stale"]}
        notes={{ empty: { text: `No cut yet. A first cut would record ${plural(d.units.live?.units.length ?? 0, "work unit")} in ${plural(d.units.live?.waves.length ?? 0, "wave")}.`, action: <span className="max-sm:hidden"><Button variant="outline" size="sm" disabled={!!off || !d.modules.modules.length} onClick={doCut}>Cut work units</Button></span> } }}>
        <StepPanel title="1 · Cut" side={<>
          <span data-value=""><b>{d.units.cut ? (diff.length ? `${plural(diff.length, "change")} since cut ${d.units.cut.seq}` : `cut ${d.units.cut.seq} matches the graph`) : "no cut taken"}</b></span>
          <span data-value="" className="text-muted-foreground">last cut: {d.units.cut ? `cut ${d.units.cut.seq}, ${M.when(d.units.cut.at)}` : "none"}</span>
          <span data-value="" className="text-muted-foreground">{d.gates ? (d.gates.sound ? "gates sound · then step 2" : "gates unsound: a cut is refused") : "gates not read"}</span>
          <div className="max-sm:hidden"><RunButton kind="cut" phase={cut} disabledReason={off ?? (state === "loading" ? "loading" : !d.modules.modules.length ? "no modules declared" : undefined)} onPress={doCut} /></div>
          <Flash flash={cutFlash} />
        </>}>
          {diff.length ? <DataTable caption={`Changes since cut ${d.units.cut?.seq}`} head={[`Change since cut ${d.units.cut?.seq}`, "Unit", "Wave"]} rows={diff} />
            : <p className="m-0 text-sm text-muted-foreground">{d.units.cut ? "Nothing moved since the last cut." : "No cut to compare with."}</p>}
        </StepPanel>
      </Target>
      <Target name="ship" state={state} what="the ship" handles={["error", "partial", "offline"]}
        notes={{ offline: { text: <>Settings and history from {clock(s.updated)}, not current. Cut and Ship are off while offline.</> } }}
        errorKeeps={<StepPanel title="2 · Ship to GitHub" side={shipSide} below={progress}>{settings}</StepPanel>}>
        <StepPanel title="2 · Ship to GitHub" side={shipSide} below={progress}>{settings}</StepPanel>
      </Target>
      <section data-block="history" className="flex flex-col gap-2">
        <Surface>
          <H2>History</H2>
          <Plain state={state} empty="No cuts or ships yet.">
            <div className="flex flex-col gap-4 lg:flex-row">
              <div className="flex min-w-0 flex-1 flex-col gap-1"><h3 className="m-0 text-sm font-semibold">Cuts</h3>
                {d.cuts.length ? <DataTable caption="Cuts" head={["Cut", "Taken", "Units"]} num={[2]} rows={[...d.cuts].reverse().map(c => [`cut ${c.seq}`, M.when(c.at), c.units])} />
                  : <p className="m-0 text-sm text-muted-foreground">No cuts yet.</p>}</div>
              <div className="flex min-w-0 flex-1 flex-col gap-1"><h3 className="m-0 text-sm font-semibold">Ships</h3>
                {ships.length ? <DataTable caption="Ships" head={["Run", "Cut", "Outcome", "Created", "Failed", "Repo or reason"]} num={[3, 4]} rows={ships} />
                  : <p className="m-0 text-sm text-muted-foreground">No ships yet.</p>}</div>
            </div>
          </Plain>
        </Surface>
      </section>
    </Frame>
  )
}

export const screens = { spine: Spine, order: Order, read: Read, waves: Waves, modules: Modules, queues: Queues, ship: Ship }
