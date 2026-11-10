import type { Folder, FileRow } from "@/components/ui/file-tree"
import type { SpineData } from "@/components/ui/spine-graph"
import { layerOf } from "@/components/ui/spine-graph"
import type { Cut, ProjectData, Projection, Unit } from "@/lib/api"

// Pure derivations from what the unit answered. Nothing here projects anything the unit owns (units, waves, gates):
// it only counts, groups and walks the edges the unit sent.

/** The layer set, in column order, plus modules. tests/test_layer_set.py pins this line to the unit's ALL_LAYERS. */
export const LAYERS: [string, string][] = [["I", "intent"], ["B", "behaviour"], ["A", "architecture"], ["S", "spec"], ["T", "tests"], ["M", "modules"]]
export const LAYER_NAME: Record<string, string> = Object.fromEntries(LAYERS)
/** What the Spine draws: tests fork off spec and are listed under the graph instead. */
export const GRAPH_LAYERS = LAYERS.filter(([k]) => k !== "T")
export { layerOf }

export const isTest = (id: string) => layerOf(id) === "T"
const implOf = (impl: string[]) => impl.filter(i => !isTest(i))

/** Spine nodes and edges. Spec entries carry their test count; modules implementing only tests are not drawn. */
export function spineData(d: ProjectData): SpineData {
  const tests = testsBySpec(d)
  const nodes: SpineData["nodes"] = [], edges: SpineData["edges"] = []
  for (const e of d.spine) {
    if (isTest(e.id)) continue
    const n = tests.get(e.id)?.length ?? 0
    nodes.push([e.id, layerOf(e.id) === "S" && n ? `${e.title} · ${n} test${n === 1 ? "" : "s"}` : e.title, e.slice ?? undefined])
    for (const p of e.derives_from) edges.push([p, e.id])
    for (const q of e.depends_on) edges.push([q, e.id, "depends"])
  }
  for (const m of d.modules.modules) {
    const impl = implOf(m.implements)
    if (!impl.length) continue
    nodes.push([m.path, `implements ${impl.join(", ")}`, m.slices[0]])
    for (const s of impl) edges.push([s, m.path])
  }
  return { layers: GRAPH_LAYERS, nodes, edges }
}

/** spec id -> the test entries deriving from it. */
export function testsBySpec(d: ProjectData): Map<string, { id: string; title: string }[]> {
  const out = new Map<string, { id: string; title: string }[]>()
  for (const e of d.spine) if (isTest(e.id)) for (const s of e.derives_from) (out.get(s) ?? out.set(s, []).get(s)!).push({ id: e.id, title: e.title })
  return out
}

/** Everything that derives from the entry, down every layer: its blast radius. */
export function downFrom(id: string, edges: SpineData["edges"]): Set<string> {
  const kids = new Map<string, string[]>()
  for (const [a, b, k] of edges) if (!k || k === "derives") (kids.get(a) ?? kids.set(a, []).get(a)!).push(b)
  const out = new Set<string>(), stack = [id]
  while (stack.length) for (const n of kids.get(stack.pop()!) ?? []) if (!out.has(n)) { out.add(n); stack.push(n) }
  return out
}

export const countBy = <T,>(xs: T[], key: (x: T) => string) => xs.reduce((m, x) => m.set(key(x), (m.get(key(x)) ?? 0) + 1), new Map<string, number>())

/** Slices, sorted, with the entries in each (B, A, S and modules; intent is never sliced and tests take their spec's). */
export function slicesOf(d: ProjectData): [string, number][] {
  const m = countBy(d.spine.filter(e => e.slice && "BAS".includes(layerOf(e.id))), e => e.slice!)
  return [...m.entries()].sort(([a], [b]) => a.localeCompare(b))
}

/** The work units the Order and Ship views show: the stored cut when one exists (what was handed out), else the live
 *  projection (what a cut would record). */
export const unitSource = (d: ProjectData): (Projection & Partial<Cut>) | null => d.units.cut ?? d.units.live ?? null

/** A unit's name for a person: its anchor entry, "tests" for a test unit, and its first file. */
export function unitLabel(u: Unit | undefined, key: string): string {
  if (!u) return key
  const files = u.modules.length ? ` + ${u.modules[0]}${u.modules.length > 1 ? ` +${u.modules.length - 1}` : ""}` : ""
  return `${u.anchor}${u.tests ? " tests" : ""}${files}`
}

/** Files written by more than one unit: they must be taken one at a time. */
export function sharedFiles(src: Projection | null): number {
  const n = countBy((src?.units ?? []).flatMap(u => u.modules), f => f)
  return [...n.values()].filter(v => v > 1).length
}

/** The module list as the developer's file tree. */
export function treeOf(project: string, d: ProjectData): Folder {
  const root: Folder = { name: `${project}/`, files: [], folders: [] }
  for (const m of [...d.modules.modules].sort((a, b) => a.path.localeCompare(b.path))) {
    const parts = m.path.split("/"), name = parts.pop()!
    let at = root
    for (const p of parts) at = at.folders.find(f => f.name === `${p}/`) ?? (at.folders.push({ name: `${p}/`, files: [], folders: [] }), at.folders[at.folders.length - 1])
    const row: FileRow = { name, path: m.path, spec: implOf(m.implements).length, test: m.implements.filter(isTest).length, slices: m.slices.length }
    at.files.push(row)
  }
  return root
}

/** What changed since the stored cut, one row per moved unit: [change, unit, wave in the live projection]. */
export function cutDiff(d: ProjectData): [string, string, string][] {
  const live = d.units.live, cut = d.units.cut
  const liveUnit = new Map((live?.units ?? []).map(u => [u.key, u])), cutUnit = new Map((cut?.units ?? []).map(u => [u.key, u]))
  const waveOf = new Map((live?.waves ?? []).flatMap(w => w.units.map(k => [k, w.wave] as const)))
  return (d.units.drift?.changed ?? []).map(({ was, now }) => {
    const change = !was.length ? "new" : !now.length ? "gone" : was.length === 1 && now.length > 1 ? "split" : now.length === 1 && was.length > 1 ? "joined" : "changed"
    const name = now.length ? now.map(k => unitLabel(liveUnit.get(k), k)).join(", ") : was.map(k => unitLabel(cutUnit.get(k), k)).join(", ")
    const wave = now.length ? [...new Set(now.map(k => waveOf.get(k)).filter(w => w !== undefined))].join(", ") || "—" : "—"
    return [change, name, wave]
  })
}

export type Ready = { repo: boolean; cut: boolean; sound: boolean; fresh: boolean; token: boolean; moved: number; seq: number | null; units: number }
/** What has to hold before Ship can be pressed, each met or not. The token is a boolean the unit reports: its value
 *  never reaches this page. */
export function shipReady(d: ProjectData): Ready {
  const cut = d.units.cut, dr = d.units.drift
  const moved = dr ? dr.changed.length || (dr.edges_changed ? 1 : 0) : 0
  return { repo: !!d.repo, cut: !!cut, sound: !!d.gates?.sound, fresh: !!cut && !moved, token: !!d.emission.token, moved: dr?.changed.length ?? 0, seq: cut?.seq ?? null, units: cut?.units.length ?? 0 }
}

export type Horizon = { mode: "more"; waves: number } | { mode: "shipped" } | { mode: "one"; unit: string }
/** What a press would create, computed as the old page did: `already` is scoped to the current cut and repo by the
 *  unit, and the "shipped" press is counted by the unit itself (`shipped.create`). */
export function preflight(d: ProjectData, h: Horizon) {
  const cut = d.units.cut, keys = (cut?.units ?? []).map(u => u.key), already = d.emission.already ?? {}
  const out = keys.filter(k => already[k]).length
  let create = 0, span = 0
  if (h.mode === "one") create = h.unit && keys.includes(h.unit) && !already[h.unit] ? 1 : 0
  else if (h.mode === "shipped") create = d.emission.shipped?.create.length ?? 0
  else {
    const open = (cut?.waves ?? []).map(w => w.units).filter(w => w.some(k => !already[k]))
    create = open.slice(0, h.waves).flat().filter(k => !already[k]).length
    span = Math.min(h.waves, open.length)
  }
  return { total: keys.length, already: out, create, held: Math.max(0, keys.length - out - create), waves: span }
}

/** A unit timestamp (seconds since the epoch) for a person, in their own locale and time zone. */
export function when(at: number | null | undefined): string {
  if (!at) return "—"
  const t = new Date(at * 1000)
  return isNaN(t.getTime()) ? "—" : t.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}
