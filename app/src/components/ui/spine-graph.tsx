import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { flushSync } from "react-dom"
import { cn } from "@/lib/utils"

// The Spine: one pannable, zoomable canvas, like the production mu-spec graph (dashboard.html): drag to move, wheel or
// pinch to zoom around the pointer, +/−/fit buttons, continuous zoom. Layers run across (intent centred in its own
// column), slices run down as rounded rows shared by behaviour, architecture, spec and modules, the slice label inside
// the row, top-left. Tests are never drawn. Level of detail follows the zoom: far, each slice row shows its entry count
// per layer; mid, node ids; close, ids and titles. Only what is in view is drawn (culling), so a 924-entry project
// pans as smoothly as a small one. Focus turns the entry's chain brand, dims the rest in place, and fits the chain.
export type EdgeKind = "derives" | "parent" | "depends" | "contradicts"
export type SpineData = {
  layers: [string, string][]               // [key, name]: I, B, A, S, M
  nodes: [string, string, string?][]       // [id, title, slice]; intent has no slice; all the graph needs on load
  edges: [string, string, EdgeKind?][]     // [from, to, kind]; derives-from (parent → child) when kind is left out
}
/** Starting zoom per variant (far, mid, close); focus fits the focused chain instead. */
const ZOOM = {
  far: { k: 0.15, says: "Far: each slice is one rounded group with its entry count per layer" },
  mid: { k: 0.3, says: "Mid: every entry as a rounded node with its id" },
  close: { k: 1, says: "Close: every entry with its id and title" },
  focus: { k: 1, says: "Focus: the entry's chain in brand, the rest dimmed in place" },
}
const FAR = 0.25, MIN = 0.05, MAX = 2.4                     // below FAR slices fold to counts
const NW = 220, NH = 100, GAP = 16, COL = 340, HEAD = 64, PADB = 18, ROWGAP = 32, RP = 20, TOP = 64, M = 24
const LINE = 14, FS = 12                                   // text: px on screen at zoom ≤ 1, grows with the zoom above it
const SLICE = [
  { row: "fill-slice-1/20 stroke-slice-1", text: "fill-slice-1", node: "stroke-slice-1" }, { row: "fill-slice-2/20 stroke-slice-2", text: "fill-slice-2", node: "stroke-slice-2" },
  { row: "fill-slice-3/20 stroke-slice-3", text: "fill-slice-3", node: "stroke-slice-3" }, { row: "fill-slice-4/20 stroke-slice-4", text: "fill-slice-4", node: "stroke-slice-4" },
  { row: "fill-slice-5/20 stroke-slice-5", text: "fill-slice-5", node: "stroke-slice-5" }, { row: "fill-slice-6/20 stroke-slice-6", text: "fill-slice-6", node: "stroke-slice-6" },
  { row: "fill-slice-7/20 stroke-slice-7", text: "fill-slice-7", node: "stroke-slice-7" }, { row: "fill-slice-8/20 stroke-slice-8", text: "fill-slice-8", node: "stroke-slice-8" },
  { row: "fill-slice-9/20 stroke-slice-9", text: "fill-slice-9", node: "stroke-slice-9" }, { row: "fill-slice-10/20 stroke-slice-10", text: "fill-slice-10", node: "stroke-slice-10" },
]
const SLICE_NONE = { row: "fill-slice-none/20 stroke-slice-none", text: "fill-slice-none", node: "stroke-slice-none" }
const R_ROW = 8, R_NODE = 6                                // px on screen: --radius-lg (slices), --radius-md (nodes)
const layerOf = (id: string) => (id.includes("·") ? id.split("·")[0] : "M")
const clip = (s: string, n: number) => (n < 2 ? "" : s.length > n ? `${s.slice(0, n - 1)}…` : s)
const DASH: Record<EdgeKind, string | undefined> = { derives: undefined, parent: "2 4", depends: "7 5", contradicts: undefined }

/** Word-wrap a node title into at most `lines` lines of `n` characters; what does not fit ends in an ellipsis, never a
 *  mid-word cut. */
export function wrap(t: string, n = 20, lines = 2): string[] {
  const out: string[] = []
  let cur = ""
  for (const w of t.split(/\s+/).filter(Boolean)) {
    if (!cur || `${cur} ${w}`.length <= n) cur = cur ? `${cur} ${w}` : w
    else { out.push(cur); cur = w }
  }
  if (cur) out.push(cur)
  const kept = out.slice(0, lines).map(l => (l.length > n ? `${l.slice(0, n - 1)}…` : l))
  if (out.length > lines) { const last = kept[lines - 1]; kept[lines - 1] = `${last.length > n - 1 ? last.slice(0, n - 1) : last}…` }
  return kept
}

/** The entry and everything it derives from or that derives from it, in any layer. */
export function chainOf(id: string, edges: SpineData["edges"]): Set<string> {
  const up = new Map<string, string[]>(), down = new Map<string, string[]>()
  for (const [a, b, k] of edges) if ((k ?? "derives") === "derives") { (down.get(a) ?? down.set(a, []).get(a)!).push(b); (up.get(b) ?? up.set(b, []).get(b)!).push(a) }
  const out = new Set([id])
  for (const m of [up, down]) { const stack = [id]; while (stack.length) for (const n of m.get(stack.pop()!) ?? []) if (!out.has(n)) { out.add(n); stack.push(n) } }
  return out
}

type Box = { x0: number; y0: number; x1: number; y1: number }
type Row = { slice: string; x: number; y: number; w: number; h: number; counts: number[] }
/** Positions once per data set (top-left of each node, world units). Inside a row each column is ordered by where its
 *  parents sit, so derives edges cross less; a shorter column is centred in its row, as the production graph does.
 *  Intent is placed by its connections, not its number (Andrey, stage 6): each intent docks level with its home slice
 *  (the one holding most of its children), and slices that share intents sit next to each other. */
export function layoutSpine(d: SpineData) {
  const colOf = new Map(d.layers.map(([k], i) => [k, i])), last = d.layers.length - 1
  const colX = (c: number) => c * COL, span = (n: number) => n * NH + Math.max(0, n - 1) * GAP
  const parents = new Map<string, string[]>()
  for (const [a, b, k] of d.edges) if ((k ?? "derives") === "derives") (parents.get(b) ?? parents.set(b, []).get(b)!).push(a)
  const intent: string[] = [], groups = new Map<string, string[][]>(), sliceOf = new Map<string, string>()
  for (const [id, , s] of d.nodes) {
    const c = colOf.get(layerOf(id)); if (c === undefined) continue // tests and unknown layers are never drawn
    if (c === 0) { intent.push(id); continue }
    sliceOf.set(id, s || "unsliced")
    const g = groups.get(s || "unsliced") ?? groups.set(s || "unsliced", d.layers.map(() => [])).get(s || "unsliced")!
    g[c].push(id)
  }
  // Each intent's children per slice; its home is the slice with most (ties: the slice met first in the data).
  const isIntent = new Set(intent), feeds = new Map(intent.map(id => [id, new Map<string, number>()]))
  for (const [a, b, k] of d.edges) { const sl = sliceOf.get(b); if ((k ?? "derives") === "derives" && isIntent.has(a) && sl) feeds.get(a)!.set(sl, (feeds.get(a)!.get(sl) ?? 0) + 1) }
  const dataOrder = [...groups.keys()], home = new Map<string, string>()
  for (const [id, m] of feeds) { let best: string | undefined; for (const sl of dataOrder) if ((m.get(sl) ?? 0) > (best ? m.get(best)! : 0)) best = sl; if (best) home.set(id, best) }
  // Slice order: start with the slice most intents feed, then keep adding the one sharing most intents with the last
  // placed (then with all placed), so an intent's secondary edges travel a row or two, not across the graph.
  const shared = (x: string, y: string) => intent.reduce((n, id) => n + Number(feeds.get(id)!.has(x) && feeds.get(id)!.has(y)), 0)
  const fed = (x: string) => intent.reduce((n, id) => n + Number(feeds.get(id)!.has(x)), 0)
  const left = dataOrder.filter(x => x !== "unsliced"), order: string[] = []
  while (left.length) {
    const prev = order[order.length - 1]
    const score = (x: string) => prev === undefined ? [fed(x), 0] : [shared(prev, x), order.reduce((n, o) => n + shared(o, x), 0)]
    let bi = 0; left.forEach((x, i) => { const [p, q] = score(x), [bp, bq] = score(left[bi]); if (p > bp || (p === bp && q > bq)) bi = i })
    order.push(...left.splice(bi, 1))
  }
  if (groups.has("unsliced")) order.push("unsliced")
  const pos = new Map<string, { x: number; y: number }>(), rank = new Map(intent.map((id, i) => [id, i]))
  const rows: Row[] = []
  let y = TOP
  for (const slice of order) {
    const cols = groups.get(slice)!, docked = intent.filter(id => home.get(id) === slice)
    const tall = Math.max(docked.length, ...cols.map(c => c.length)), h = HEAD + span(tall) + PADB
    docked.forEach((id, i) => pos.set(id, { x: 0, y: y + HEAD + (span(tall) - span(docked.length)) / 2 + i * (NH + GAP) }))
    cols.forEach((ids, c) => {
      const key = (id: string) => { const ps = (parents.get(id) ?? []).map(p => pos.get(p)?.y ?? rank.get(p)).filter((v): v is number => v !== undefined); return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : Infinity }
      const sorted = ids.map((id, i) => [id, key(id), i] as const).sort((a, b) => a[1] - b[1] || a[2] - b[2])
      const top = y + HEAD + (span(tall) - span(ids.length)) / 2
      sorted.forEach(([id], i) => pos.set(id, { x: colX(c), y: top + i * (NH + GAP) }))
    })
    rows.push({ slice, x: colX(1) - RP, y, w: colX(last) + NW + RP - (colX(1) - RP), h, counts: cols.map(c => c.length) })
    y += h + ROWGAP
  }
  // An intent that feeds no slice yet stacks below the rows.
  intent.filter(id => !home.has(id)).forEach(id => { pos.set(id, { x: 0, y }); y += NH + GAP })
  const height = Math.max(TOP, y - (intent.some(id => !home.has(id)) ? GAP : ROWGAP))
  // Far: intent → slice row, one line per pair, as thick as the entries it carries.
  const rowOf = new Map<string, number>()
  rows.forEach((r, ri) => groups.get(r.slice)!.flat().forEach(id => rowOf.set(id, ri)))
  const fan = new Map<string, number>()
  for (const [a, b, k] of d.edges) if ((k ?? "derives") === "derives" && rank.has(a) && rowOf.has(b)) { const key = `${a}|${rowOf.get(b)}`; fan.set(key, (fan.get(key) ?? 0) + 1) }
  const layerCounts = d.layers.map((_, c) => (c === 0 ? intent.length : rows.reduce((a, r) => a + r.counts[c], 0)))
  return { pos, rows, fan, colX, width: colX(last) + NW, height, rowOf, layerCounts, sliceNames: order.filter(x => x !== "unsliced").sort(), sliceOf: new Map(d.nodes.map(n => [n[0], n[2] || "unsliced"])) }
}

type View = { x: number; y: number; k: number }
const clampK = (k: number) => Math.min(MAX, Math.max(MIN, k))
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches

function SpineGraph({ data, zoom = "mid", focus, edges = ["derives"], hrefFor, sliceHref, missing = {}, height = 560, toolbar }: {
  data: SpineData; zoom?: keyof typeof ZOOM; focus?: string; edges?: EdgeKind[]; hrefFor: (id: string) => string; sliceHref: (slice: string) => string
  missing?: Record<string, string>; height?: number; toolbar?: ReactNode
}) {
  const L = useMemo(() => layoutSpine(data), [data])
  const titles = useMemo(() => new Map(data.nodes.map(([id, t]) => [id, t])), [data])
  const chain = useMemo(() => (focus && L.pos.has(focus) ? chainOf(focus, data.edges) : null), [focus, data, L])
  const frame = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: height })
  const [view, setView] = useState<View>({ x: M, y: M, k: ZOOM[zoom].k })

  // rAF-coalesced view writes: a pointer emits moves faster than frames are drawn (production dashboard, "panRAF").
  const live = useRef(view), raf = useRef(0)
  live.current = raf.current ? live.current : view
  const commit = (v: View) => { live.current = v; if (!raf.current) raf.current = requestAnimationFrame(() => { raf.current = 0; setView(live.current) }) }

  // View moves (J05: they keep his place). Zoom buttons, Fit all, Fit focus and a refocus glide on --duration-base with
  // --ease-out: the graph is drawn at the new view at once and a CSS transition on `mover` carries it from the old one
  // (FLIP), so nodes are never re-laid out mid-move. A new glide mid-move starts from where the graph is on screen
  // (retargets). Wheel, drag, pinch and keys are direct: they first settle any glide where it is. Reduced motion: instant.
  const mover = useRef<SVGGElement>(null), flip = useRef<View | null>(null)
  const [from, setFrom] = useState<View | null>(null), gliding = useRef(false)
  gliding.current = !!from
  /** The view as it is on screen now, part way through a glide included. */
  const onScreen = (): View => {
    const v = live.current, g = mover.current, t = g && gliding.current ? getComputedStyle(g).transform : "none"
    const m = t.startsWith("matrix(") ? t.slice(7, -1).split(",").map(Number) : null
    return m ? { k: m[0] * v.k, x: m[0] * v.x + m[4], y: m[3] * v.y + m[5] } : v
  }
  /** Stop a glide where it is on screen (a wheel, drag or key takes over from there). */
  const settle = () => {
    const g = mover.current; if (!gliding.current || !g) return
    const v = onScreen(); live.current = v
    flushSync(() => { setFrom(null); setView(v) })
    g.style.transition = "none"; g.style.transform = ""; void getComputedStyle(g).transform; g.style.transition = ""
  }
  const glide = (v: View) => {
    if (reduced() || !size.w) { settle(); live.current = v; setView(v); return }
    const f = onScreen(); flip.current = f; setFrom(f); live.current = v; setView(v)
  }
  useLayoutEffect(() => {
    const g = mover.current, f = flip.current; if (!g || !f) return
    flip.current = null
    const r = f.k / view.k, tx = f.x - view.x * r, ty = f.y - view.y * r
    g.style.transition = "none"
    if (Math.abs(r - 1) < 1e-3 && Math.abs(tx) < 0.5 && Math.abs(ty) < 0.5) { g.style.transform = ""; g.style.transition = ""; setFrom(null); return }
    g.style.transform = `translate(${tx}px, ${ty}px) scale(${r})`
    void getComputedStyle(g).transform // start from the old view
    g.style.transition = ""; g.style.transform = ""
  })

  const fitBox = (b: Box, maxK: number, w = size.w, h = size.h) => {
    const k = clampK(Math.min(maxK, (w - 2 * M) / (b.x1 - b.x0), (h - 2 * M) / (b.y1 - b.y0)))
    const dw = (b.x1 - b.x0) * k, dh = (b.y1 - b.y0) * k
    // Centre what fits; anchor what does not to its own start (the top of the graph is where reading starts).
    return { k, x: dw > w - 2 * M ? M - b.x0 * k : (w - dw) / 2 - b.x0 * k, y: dh > h - 2 * M ? M - b.y0 * k : (h - dh) / 2 - b.y0 * k }
  }
  const whole: Box = { x0: -M, y0: 0, x1: L.width + M, y1: L.height }
  const chainBox = (): Box | null => {
    if (!chain) return null
    const ps = [...chain].map(id => L.pos.get(id)).filter(p => !!p)
    return { x0: Math.min(...ps.map(p => p.x)) - 8, y0: Math.min(...ps.map(p => p.y)) - 8, x1: Math.max(...ps.map(p => p.x)) + NW + 8, y1: Math.max(...ps.map(p => p.y)) + NH + 8 }
  }
  // Size first, then the opening view: focus fits its chain; far fits the whole graph; mid and close open at the first
  // slice row. Refocusing refits; new data never takes the reader's pan and zoom away.
  useLayoutEffect(() => {
    const el = frame.current!; const read = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    read(); const ro = new ResizeObserver(read); ro.observe(el); return () => ro.disconnect()
  }, [])
  const opened = useRef(false), lastFocus = useRef(focus)
  useLayoutEffect(() => {
    if (!size.w) return
    const refocus = opened.current && lastFocus.current !== focus; opened.current = true; lastFocus.current = focus
    const cb = chainBox(), r = L.rows[0]
    if (cb && refocus) glide(fitBox(cb, 1))
    else if (cb) setView(fitBox(cb, 1))
    else if (zoom === "far") {
      // The whole graph when it fits at a readable far zoom; else far's own zoom, centred across, from the top.
      const v = fitBox(whole, FAR - 0.01), k = ZOOM.far.k
      setView(v.k >= k ? v : { k, x: (size.w - (whole.x1 - whole.x0) * k) / 2 - whole.x0 * k, y: M })
    } else if (zoom === "close" && r) {
      // Close opens on the first slice's first behaviour entry, where reading starts.
      const ys = [...L.pos].filter(([id, p]) => L.rowOf.get(id) === 0 && p.x === L.colX(1)).map(([, p]) => p.y)
      const k = ZOOM.close.k; setView({ k, x: M - r.x * k, y: M - ((ys.length ? Math.min(...ys) : r.y + HEAD) - 40) * k })
    } else { const k = ZOOM[zoom].k; setView({ k, x: M + M * k, y: M }) }
  }, [focus, L, size.w > 0])

  const zoomed = (mx: number, my: number, f: number): View => { const v = live.current, k = clampK(v.k * f); return { k, x: mx - (mx - v.x) * (k / v.k), y: my - (my - v.y) * (k / v.k) } }
  const zoomAt = (mx: number, my: number, f: number) => { settle(); commit(zoomed(mx, my, f)) }
  useLayoutEffect(() => {
    const el = frame.current!
    const wheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = el.getBoundingClientRect(), dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1)
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015))) // ctrl: trackpad pinch
    }
    el.addEventListener("wheel", wheel, { passive: false })
    return () => el.removeEventListener("wheel", wheel)
  }, [])
  // Drag anywhere (on a node too) moves the view; a press that travels under 4px stays a click. Two pointers pinch.
  const ptrs = useRef(new Map<number, { x: number; y: number }>()), drag = useRef<{ x: number; y: number; moved: number } | null>(null)
  const pinch = useRef<{ d: number } | null>(null)
  const [panning, setPanning] = useState(false)
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (ptrs.current.size === 2) { const [a, b] = [...ptrs.current.values()]; pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y) } }
    drag.current = { x: e.clientX, y: e.clientY, moved: 0 }
  }
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const p = ptrs.current.get(e.pointerId); if (!p) return
    const dx = e.clientX - p.x, dy = e.clientY - p.y
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pinch.current && ptrs.current.size === 2) {
      const [a, b] = [...ptrs.current.values()], d = Math.hypot(a.x - b.x, a.y - b.y), r = e.currentTarget.getBoundingClientRect()
      zoomAt((a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top, d / pinch.current.d); pinch.current.d = d; return
    }
    const g = drag.current; if (!g) return
    g.moved = Math.abs(e.clientX - g.x) + Math.abs(e.clientY - g.y)
    if (g.moved < 4) return
    if (!panning) { settle(); e.currentTarget.setPointerCapture(e.pointerId); setPanning(true) }
    const v = live.current; commit({ ...v, x: v.x + dx, y: v.y + dy })
  }
  const endPan = (e: React.PointerEvent) => {
    ptrs.current.delete(e.pointerId); if (ptrs.current.size < 2) pinch.current = null
    if (!ptrs.current.size) { setPanning(false); setTimeout(() => { drag.current = null }) } // the click, if any, comes first
  }
  const onClickCapture = (e: React.MouseEvent) => { if ((drag.current?.moved ?? 0) >= 4) { e.preventDefault(); e.stopPropagation() } }
  const centre = (f: number) => zoomAt(size.w / 2, size.h / 2, f)          // keys: instant
  const centreGlide = (f: number) => glide(zoomed(size.w / 2, size.h / 2, f)) // buttons: glide
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.target === e.currentTarget) settle()
    const v = live.current, s = 64
    const keys: Record<string, () => void> = {
      ArrowLeft: () => commit({ ...v, x: v.x + s }), ArrowRight: () => commit({ ...v, x: v.x - s }), ArrowUp: () => commit({ ...v, y: v.y + s }), ArrowDown: () => commit({ ...v, y: v.y - s }),
      "+": () => centre(1.25), "=": () => centre(1.25), "-": () => centre(0.8), "0": () => { live.current = fitBox(whole, 1); setView(live.current) },
    }
    if (keys[e.key] && e.target === e.currentTarget) { e.preventDefault(); keys[e.key]() }
  }
  // Tabbing to a node outside the frame pans it into view (the frame itself never scrolls).
  const onFocus = (e: React.FocusEvent) => {
    const t = (e.target as Element).closest("[data-node]"), c = frame.current; if (!t || !c) return
    const r = t.getBoundingClientRect(), f = c.getBoundingClientRect()
    const dx = r.left < f.left + M ? f.left + M - r.left : r.right > f.right - M ? f.right - M - r.right : 0
    const dy = r.top < f.top + M ? f.top + M - r.top : r.bottom > f.bottom - M ? f.bottom - M - r.bottom : 0
    if (dx || dy) { settle(); const v = live.current; commit({ ...v, x: v.x + dx, y: v.y + dy }) }
  }

  // ---- what is in view ----
  const { k } = view, s = 1 / Math.min(k, 1), far = k < FAR
  const box = (v: View): Box => ({ x0: -v.x / v.k, y0: -v.y / v.k, x1: (size.w - v.x) / v.k, y1: (size.h - v.y) / v.k })
  // While gliding, draw what either end of the move shows, so nothing pops in at the edges mid-move.
  const vr: Box = from ? (([a, b]) => ({ x0: Math.min(a.x0, b.x0), y0: Math.min(a.y0, b.y0), x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1) }))([box(view), box(from)]) : box(view)
  const pad = 40 / k, near = (x0: number, y0: number, x1: number, y1: number) => x1 >= vr.x0 - pad && x0 <= vr.x1 + pad && y1 >= vr.y0 - pad && y0 <= vr.y1 + pad
  const inView = (x: number, y: number) => x >= vr.x0 && x <= vr.x1 && y >= vr.y0 && y <= vr.y1 // text is drawn only when its anchor is on screen
  const lit = (id: string) => !chain || chain.has(id)
  const lines = Math.min(3, Math.floor((NH * k - 4) / LINE)), chars = (w: number) => Math.floor((w * Math.min(k, 1) - 12) / (FS * 0.6))
  const isIntent = (id: string) => L.rowOf.get(id) === undefined

  const rowEls: ReactNode[] = [], edgeEls: ReactNode[] = [], nodeEls: ReactNode[] = [], textEls: ReactNode[] = []
  const hueOf = (slice: string) => slice === "unsliced" ? SLICE_NONE : SLICE[(L.sliceNames.indexOf(slice) + 1) % SLICE.length] // slice-1 (teal) comes last: teal is the focus colour
  L.rows.forEach((r, ri) => {
    if (!near(r.x, r.y, r.x + r.w, r.y + r.h)) return
    const total = r.counts.reduce((a, b) => a + b, 0), dim = !!chain && ![...chain].some(id => L.rowOf.get(id) === ri)
    const lfs = far ? FS * s : Math.min(FS * s, HEAD - 16), lx = Math.max(r.x + 12 * s, vr.x0 + 8 * s), ly = r.y + (far ? 26 * s : lfs + 8), n = chars(r.x + r.w - lx)
    rowEls.push(<rect key={`r${ri}`} x={r.x} y={r.y} width={r.w} height={r.h} rx={Math.min(R_ROW * s, r.h / 2)} vectorEffect="non-scaling-stroke"
      className={cn(hueOf(r.slice).row, dim && "[fill-opacity:0.1] [stroke-opacity:0.6]")} />)
    if (inView(lx, ly) && n >= 3 && (!far || r.h * k >= 32)) textEls.push(
      <a key={`rl${ri}`} href={sliceHref(r.slice)} aria-label={`${r.slice}: ${total} entries; open in Read`} className="outline-none focus-visible:[&>text]:underline">
        <text x={lx} y={ly} fontSize={lfs} fontWeight={600} className={cn(hueOf(r.slice).text, dim && "[fill-opacity:0.6]")}>{clip(r.slice, n)}</text>
      </a>)
    if (far) r.counts.forEach((c, ci) => {
      if (!c) return
      const cx = L.colX(ci) + NW / 2, cy = r.y + r.h / 2 + 6 * s
      if (inView(cx, cy) && COL * k >= 20 && r.h * k >= 76) textEls.push(<text key={`rc${ri}-${ci}`} x={cx} y={cy} fontSize={(FS + 2) * s} textAnchor="middle" className="fill-foreground tabular-nums">{c}</text>)
    })
  })
  // Column heads: the layer's name, shortened to its letter when the column is narrow on screen.
  const heads = data.layers.map(([key, name], ci) => {
    const count = L.layerCounts[ci], full = `${name} · ${count}`, x = L.colX(ci), y = TOP - 16 * s
    const label = COL * k >= full.length * 7.5 ? full : COL * k >= 14 ? key : ""
    return label && inView(x, y) ? <text key={`h${key}`} x={x} y={y} fontSize={(FS + 1) * s} fontWeight={600} className="fill-foreground">{label}</text> : null
  })

  const P = (id: string) => L.pos.get(id)
  if (far) {
    for (const [key, n] of L.fan) {
      const [a, ri] = key.split("|"), p = P(a)!, r = L.rows[Number(ri)], x2 = r.x, y2 = r.y + r.h / 2, x1 = p.x + NW, y1 = p.y + NH / 2
      if (!near(Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2))) continue
      const on = !!chain && chain.has(a) && [...chain].some(id => L.rowOf.get(id) === Number(ri))
      edgeEls.push(<path key={`f${key}`} d={`M ${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}`} fill="none" vectorEffect="non-scaling-stroke"
        strokeWidth={on ? 2 : Math.min(4, 0.75 + Math.log2(n))} className={cn(on ? "stroke-primary" : "stroke-subtle", chain && !on && "[stroke-opacity:0.4]")} />)
    }
  } else {
    for (const [a, b, kind = "derives"] of data.edges) {
      if (!edges.includes(kind)) continue
      const p = P(a), q = P(b); if (!p || !q) continue
      const same = p.x === q.x, gx = p.x - 40 - (Math.abs(q.y - p.y) / NH) * 2
      const [x1, y1, x2, y2] = same ? [p.x, p.y + NH / 2, q.x, q.y + NH / 2] : [p.x + NW, p.y + NH / 2, q.x, q.y + NH / 2]
      if (!near(Math.min(x1, x2, same ? gx : x1), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2))) continue
      const on = kind === "derives" && !!chain && chain.has(a) && chain.has(b)
      const d = same ? `M ${x1} ${y1} C ${gx} ${y1}, ${gx} ${y2}, ${x2} ${y2}` : `M ${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}`
      edgeEls.push(<path key={`e${a}>${b}>${kind}`} d={d} fill="none" vectorEffect="non-scaling-stroke" strokeDasharray={DASH[kind]} strokeWidth={on ? 2 : kind === "derives" ? 1 : 1.25}
        className={cn(on ? "stroke-primary" : kind === "contradicts" ? "stroke-status-down" : "stroke-subtle", chain && !on && "[stroke-opacity:0.3]")} />)
    }
  }
  for (const [id, p] of L.pos) {
    if (far && !isIntent(id)) continue
    if (!near(p.x, p.y, p.x + NW, p.y + NH)) continue
    const sel = id === focus, on = !!chain && chain.has(id), gap = missing[id], title = gap ?? titles.get(id) ?? ""
    const label = layerOf(id) === "M" ? id.split("/").pop()! : id
    const showText = inView(p.x + NW / 2, p.y + NH / 2) && lines >= 1 && NW * k >= 36
    const titleLines = showText && lines >= 2 ? wrap(title, chars(NW), Math.min(2, lines - 1)) : []
    nodeEls.push(
      <a key={id} href={hrefFor(id)} data-node="" aria-label={`${id} ${title}${sel ? " (focused)" : ""}`.trim()} aria-current={sel || undefined}
        className={cn("outline-none focus-visible:[&>rect]:stroke-ring focus-visible:[&>rect]:[stroke-width:3px]", !lit(id) && "opacity-40")}>
        <title>{`${id} · ${title}`}</title>
        <rect x={p.x} y={p.y} width={NW} height={NH} rx={Math.min(R_NODE * s, NH / 2)} vectorEffect="non-scaling-stroke" strokeWidth={sel ? 3 : on ? 2 : 1}
          strokeDasharray={gap ? "4 3" : undefined} className={cn(sel ? "fill-primary/15" : "fill-card", on ? "stroke-primary" : isIntent(id) ? "stroke-subtle" : hueOf(L.sliceOf.get(id) ?? "unsliced").node)} />
        {showText && <text x={p.x + 8 * s} y={lines >= 2 ? p.y + 15 * s : p.y + NH / 2 + 4 * s} fontSize={FS * s} fontWeight={600} className="fill-foreground font-mono">{clip(label, chars(NW))}</text>}
        {titleLines.map((l, i) => <text key={i} x={p.x + 8 * s} y={p.y + (15 + LINE * (i + 1)) * s} fontSize={FS * s} className={gap ? "fill-muted-foreground italic" : "fill-muted-foreground"}>{l}</text>)}
      </a>)
  }

  const btn = "flex size-8 items-center justify-center rounded-md border border-border bg-card text-sm text-foreground outline-none transition-colors duration-(--duration-fast) ease-(--ease-standard) hover:bg-accent active:bg-border focus-visible:ring-[3px] focus-visible:ring-ring/50"
  const lod = far ? "far" : lines >= 3 ? "close" : "mid"
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">{toolbar}</div>
        <div className="flex items-center gap-1">
          <span className="mr-1 text-xs text-muted-foreground tabular-nums" aria-live="polite">{Math.round(k * 100)}% · {lod}</span>
          <button type="button" className={btn} aria-label="Zoom in" onClick={() => centreGlide(1.25)}>+</button>
          <button type="button" className={btn} aria-label="Zoom out" onClick={() => centreGlide(0.8)}>−</button>
          <button type="button" className={cn(btn, "w-auto px-2")} onClick={() => glide(fitBox(whole, 1))}>Fit all</button>
          {chain && <button type="button" className={cn(btn, "w-auto px-2")} onClick={() => { const b = chainBox(); if (b) glide(fitBox(b, 1)) }}>Fit focus</button>}
        </div>
      </div>
      <div ref={frame} data-viewport="" data-lod={lod} role="region" tabIndex={0} aria-label={`Spine graph, ${ZOOM[focus ? "focus" : lod].says}. Drag to move, scroll or pinch to zoom, arrow keys move.`}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endPan} onPointerCancel={endPan} onClickCapture={onClickCapture} onDragStart={e => e.preventDefault()} onKeyDown={onKeyDown} onFocus={onFocus}
        className={cn("relative touch-none overflow-clip overscroll-contain rounded-lg border border-border bg-background outline-none select-none focus-visible:ring-[3px] focus-visible:ring-ring/50", panning ? "cursor-grabbing" : "cursor-grab")}
        style={{ height }}>
        <svg width="100%" height="100%" className="block">
          <g ref={mover} onTransitionEnd={e => { if (e.target === e.currentTarget) setFrom(null) }} className="fill-foreground transition-transform duration-(--duration-base) ease-(--ease-out) motion-reduce:transition-none">
            <g transform={`translate(${view.x} ${view.y}) scale(${k})`} className="fill-foreground">{rowEls}{edgeEls}{nodeEls}{heads}{textEls}</g>
          </g>
        </svg>
      </div>
      <p className="m-0 text-xs text-muted-foreground">drag to move · scroll or pinch to zoom · far shows slice counts, closer shows ids, then titles</p>
    </div>
  )
}

export { SpineGraph }
