import { useSyncExternalStore } from "react"
import type { Emission } from "@/lib/run"

// Every request is RELATIVE and never begins with a slash. Served at /dashboard on the unit, a relative path lands at
// the unit's own root; framed at /dashboard/mu-spec/ through the node's proxy, the same string lands inside the proxied
// prefix. An absolute path would reach the NODE, which answers with plausible JSON of the wrong shape.

export const enc = encodeURIComponent

// ---------- reads ----------
/** A read that failed: `status` 0 means the unit could not be reached at all (offline), else the HTTP status. */
export class ReadError extends Error {
  constructor(readonly path: string, readonly status: number) { super(status ? `${path} answered ${status}` : `${path}: the unit could not be reached`) }
}
export async function get<T>(path: string): Promise<T> {
  let r: Response
  try { r = await fetch(path, { headers: { accept: "application/json" } }) } catch { throw new ReadError(path, 0) }
  if (!r.ok) throw new ReadError(path, r.status)
  return r.json() as Promise<T>
}

// ---------- writes ----------
// Every POST this page makes is one of the functions in `write`, and each path is declared in the unit's /actions:
// the node's proxy refuses an undeclared path with 405 before the unit is contacted. tests/test_server.py reads this
// file and checks both directions. `actor: "dashboard"` names the press in the unit's change log.
export type Posted = { ok: boolean; status: number; payload: any; message: string }
async function post(path: string, body: Record<string, unknown>): Promise<Posted> {
  let res: Response
  try {
    res = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, actor: "dashboard" }) })
  } catch (e) {
    return { ok: false, status: 0, payload: null, message: `Could not reach this unit (${String(e)}). Nothing was written.` }
  }
  const text = await res.text()
  let payload: any = null
  try { payload = JSON.parse(text) } catch { /* the node's own refusals are plain text */ }
  return { ok: res.ok, status: res.status, payload, message: res.ok ? "" : refusal(res.status, text, payload) }
}
/** What a refused write said: this unit's JSON reason first, then the body verbatim (the node's proxy refuses an
 *  undeclared path with 405 and plain text, before the unit is contacted), the bare status only when nothing else. */
function refusal(status: number, text: string, payload: any): string {
  const said = (payload && (payload.error || payload.reason)) || text.trim().slice(0, 400)
  if (!said) return `Refused (${status})`
  return `${said} (${status})${payload?.detail ? ` ${payload.detail}` : ""}`
}

export type ShipBody = { waves?: number | "shipped"; unit?: string; build?: string }
export const write = {
  cut: (p: string) => post(`projects/${enc(p)}/units/cut`, { note: "" }),
  ratify: (p: string) => post(`projects/${enc(p)}/slicing/proposal/ratify`, {}),
  reject: (p: string, note: string) => post(`projects/${enc(p)}/slicing/proposal/reject`, { note }),
  setRepo: (p: string, repo: string | null) => post(`projects/${enc(p)}/repo`, { repo }),
  setLabels: (p: string, labels: string[], pickup?: string[]) => post(`projects/${enc(p)}/labels`, pickup ? { labels, pickup } : { labels }),
  setDelegation: (p: string, enabled: boolean) => post(`projects/${enc(p)}/delegation`, { enabled }),
  ship: (p: string, body: ShipBody) => post(`projects/${enc(p)}/emit`, body),
}

// ---------- what the unit answers (only the fields this page reads) ----------
export type SpineRow = { id: string; title: string; derives_from: string[]; depends_on: string[]; slice: string | null }
export type Unit = { key: string; anchor: string; entries: string[]; modules: string[]; slices: string[]; tests: boolean }
export type Projection = { units: Unit[]; edges: Record<string, string[]>; overlap?: Record<string, string[]>; waves: { wave: number; units: string[] }[]; unimplemented?: string[] }
export type Cut = Projection & { seq: number; at: number; note?: string }
export type Units = { cut: Cut | null; drift: { changed: { was: string[]; now: string[] }[]; edges_changed?: unknown } | null; live: Projection | null }
export type Module = { path: string; implements: string[]; slices: string[]; covered_by: string[] }
export type Modules = { modules: Module[]; untested: string[]; unimplemented_tests: string[]; uncovered_tests: string[] }
export type Message = { id: string; type: string; title: string; body?: string; status: string; at: number; project?: string }
export type Issue = { id: string; target: string; target_slice: string | null; kind: string; claim: string; status: string; at: number }
export type SliceInfo = { slice: string; size: number; cohesion: number | null; depends_on: string[] }
export type Proposal = { status: string; proposal: Record<string, string[]>; note?: string; score?: { legal?: boolean; slices?: SliceInfo[]; waves?: { wave: number; slices: string[] }[]; unassigned?: string[] } | null }
export type Event = { seq: number; at: number; kind: string; facts: Record<string, any> }
export type ShipState = Emission & { cut_seq?: number | null; already?: Record<string, unknown>; shipped?: { create: string[]; held: number }; token?: boolean; identity?: { login: string } | null; build?: string | null }

export type ProjectData = {
  project: string
  spine: SpineRow[]
  gates: { sound: boolean; complete?: boolean } | null
  waves: { wave: number; slices: string[] }[]
  slices: SliceInfo[]
  issues: Issue[]
  requests: Message[]
  modules: Modules
  proposal: Proposal | null
  units: Units
  cuts: { seq: number; at: number; note?: string; units: number }[]
  repo: string | null
  labels: string[]
  pickup: string[]
  emission: ShipState
  delegation: boolean
  ships: Event[]
}

/** Read every route a view draws from. The spine is the one hard requirement; every other read degrades to empty and
 *  is named in `failed`, so a view can say what it lacks (partial) instead of passing absence off as "none". */
async function fetchProject(project: string): Promise<{ data: ProjectData; failed: string[] }> {
  const base = `projects/${enc(project)}`, failed: string[] = []
  const soft = <T,>(path: string, fallback: T): Promise<T> => get<T>(path).catch((e: ReadError) => { if (e.status === 0) throw e; failed.push(path); return fallback })
  const [spine, gates, waves, insights, issues, inbox, modules, proposal, units, cuts, repo, labels, emission, delegation, done, broke] = await Promise.all([
    get<{ spine: SpineRow[] }>(`${base}/spine`),
    soft<ProjectData["gates"]>(`${base}/gates`, null),
    soft<{ waves: ProjectData["waves"] }>(`${base}/waves`, { waves: [] }),
    soft<{ slices: SliceInfo[] }>(`${base}/insights`, { slices: [] }),
    soft<{ issues: Issue[] }>(`${base}/issues`, { issues: [] }),
    soft<{ messages: Message[] }>(`inbox?project=${enc(project)}`, { messages: [] }),
    soft<Partial<Modules>>(`${base}/modules`, {}),
    soft<Proposal>(`${base}/slicing/proposal`, { status: "none", proposal: {} }),
    soft<Units>(`${base}/units`, { cut: null, drift: null, live: null }),
    soft<{ cuts: ProjectData["cuts"] }>(`${base}/units/cuts`, { cuts: [] }),
    soft<{ repo: string | null }>(`${base}/repo`, { repo: null }),
    soft<{ labels: string[]; pickup: string[] }>(`${base}/labels`, { labels: [], pickup: [] }),
    soft<ShipState>(`${base}/emit`, { running: false, phase: "done" }),
    soft<{ delegation: { enabled: boolean } }>(`${base}/delegation`, { delegation: { enabled: false } }),
    soft<{ events: Event[] }>(`${base}/events?kind=tickets_emitted`, { events: [] }),
    soft<{ events: Event[] }>(`${base}/events?kind=ship_failed`, { events: [] }),
  ])
  return {
    failed,
    data: {
      project,
      spine: spine.spine ?? [],
      gates,
      waves: waves.waves ?? [],
      slices: insights.slices ?? [],
      issues: issues.issues ?? [],
      requests: inbox.messages ?? [],
      modules: { modules: modules.modules ?? [], untested: modules.untested ?? [], unimplemented_tests: modules.unimplemented_tests ?? [], uncovered_tests: modules.uncovered_tests ?? [] },
      proposal: proposal.status === "pending" ? proposal : null,
      units: { cut: units.cut ?? null, drift: units.drift ?? null, live: units.live ?? null },
      cuts: cuts.cuts ?? [],
      repo: repo.repo ?? null,
      labels: labels.labels ?? [],
      pickup: labels.pickup ?? [],
      emission,
      delegation: !!delegation.delegation?.enabled,
      ships: [...(done.events ?? []), ...(broke.events ?? [])].sort((a, b) => b.at - a.at),
    },
  }
}

// ---------- the store every view reads ----------
export type Store = {
  projects: string[] | null   // null until the first answer
  project: string | null
  data: ProjectData | null    // the last good data for `project`; kept through a failed refresh
  loading: boolean
  error: string | null        // the last refresh failed with this
  offline: boolean            // ...because the unit could not be reached at all
  failed: string[]            // optional reads that failed on the last good refresh
  updated: number | null      // when `data` last arrived (ms)
}
let store: Store = { projects: null, project: null, data: null, loading: true, error: null, offline: false, failed: [], updated: null }
const subs = new Set<() => void>()
const set = (patch: Partial<Store>) => { store = { ...store, ...patch }; subs.forEach(f => f()) }
export const useStore = () => useSyncExternalStore(f => { subs.add(f); return () => { subs.delete(f) } }, () => store)
export const getStore = () => store

const LAST = "mu-spec.project"
const remembered = () => { try { return localStorage.getItem(LAST) } catch { return null } }
const remember = (p: string) => { try { localStorage.setItem(LAST, p) } catch { /* storage is a convenience */ } }

let inFlight = 0
/** Re-read the project list and the current project. A refresh keeps what is on screen until new data lands; an
 *  unchanged answer keeps the same objects, so nothing downstream recomputes (the spine layout above all). */
export async function refresh(project = store.project) {
  const mine = ++inFlight
  set({ loading: true })
  try {
    const { projects = [] } = await get<{ projects: string[] }>("projects")
    const p = project && projects.includes(project) ? project : (remembered() && projects.includes(remembered()!) ? remembered() : projects[0]) ?? null
    if (!p) { if (mine === inFlight) set({ projects, project: null, data: null, loading: false, error: null, offline: false, failed: [], updated: Date.now() }); return }
    const { data, failed } = await fetchProject(p)
    if (mine !== inFlight) return
    const same = store.data && store.data.project === p && JSON.stringify(store.data) === JSON.stringify(data)
    if (!same) bodies.clear() // an amendment may have changed a body this page cached
    set({ projects, project: p, data: same ? store.data : data, loading: false, error: null, offline: false, failed, updated: Date.now() })
  } catch (e) {
    if (mine !== inFlight) return
    const offline = e instanceof ReadError && e.status === 0
    set({ loading: false, error: e instanceof Error ? e.message : String(e), offline, project: project ?? store.project })
  }
}

/** Switching project drops the last project's data at once: showing one project's data under another's name is the
 *  one thing worse than showing nothing. */
export function selectProject(p: string) {
  remember(p)
  set({ project: p, data: null, failed: [], error: null })
  void refresh(p)
}

// Polled like the old page: every 10 s while the tab is visible, slower after failures (capped), and never while a
// tab is hidden. The ship run has its own faster poll (lib/run).
export const POLL = 10_000
const BACKOFF_MAX = 60_000
let timer: ReturnType<typeof setTimeout> | undefined
export function startPolling() {
  const tick = async () => {
    if (document.visibilityState === "visible") await refresh()
    const wait = store.error ? Math.min(BACKOFF_MAX, POLL * 2 ** Math.min(3, ++fails)) : (fails = 0, POLL)
    timer = setTimeout(tick, wait)
  }
  let fails = 0
  clearTimeout(timer)
  void refresh().then(() => { timer = setTimeout(tick, POLL) })
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void refresh() })
}

/** One entry with its body, cached per project: bodies are pulled by identifier only when a view opens one. */
const bodies = new Map<string, Promise<{ id: string; title: string; body: string; derives_from: string[] } | null>>()
export function entry(project: string, id: string) {
  const k = `${project}|${id}`
  if (!bodies.has(k)) bodies.set(k, get<any>(`projects/${enc(project)}/entries/${enc(id)}`).catch(() => { bodies.delete(k); return null }))
  return bodies.get(k)!
}

/** One whole layer with bodies, as a reviewer reads it: one request per layer, never one per entry. */
export type Reviewed = { id: string; title: string; body: string; slice: string | null; derives_from: string[] }
export const reviewLayer = (project: string, layer: string) =>
  get<{ entries: Reviewed[] }>(`projects/${enc(project)}/review?layer=${enc(layer)}`).then(d => d.entries ?? [])

export const watchEmission = (project: string) => () => get<ShipState>(`projects/${enc(project)}/emit`)
