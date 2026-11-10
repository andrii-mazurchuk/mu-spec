import { useSyncExternalStore } from "react"

// A ship run, as GET projects/<p>/emit reports it while it runs (the unit's watchEmission polls it every 0.9 s).
// Phase 1 processes the work units (total); phase 2 declares the order between them, one GitHub call per
// dependency (to_wire, known only once wiring starts). On finish, result holds the counts and every unit that
// did not land, with its reason (the production page's shipTrouble categories).
export type Trouble = { key?: string; blocked?: string; blocker?: string; number?: number; reason: string }
export type RunResult = {
  counts: Partial<Record<"units" | "created" | "adopted" | "edited" | "swept" | "withdrawn" | "touched" | "skipped" | "failed" | "wired" | "unwired" | "dead", number>>
  failed?: Trouble[]; unwired?: Trouble[]; dead?: Trouble[]; raced?: Trouble[]; touched?: Trouble[]; reason?: string
}
export type Emission = {
  running: boolean; phase: "running" | "done" | "failed"; step?: string
  total?: number | null; processed?: number; to_wire?: number; wired?: number
  created?: number; adopted?: number; edited?: number; swept?: number; failed?: number; skipped?: number
  error?: string; result?: RunResult
}
export type Sample = { t: number; phase: 1 | 2; done: number }

export const POLL_MS = 900

/** The phase in flight, how far it is, and its percent (whole). Phase 2 only once to_wire is known. */
export function phaseOf(e: Emission): { phase: 1 | 2; done: number; total: number | null; pct: number } {
  const wiring = e.step === "wiring" && (e.to_wire ?? 0) > 0
  const total = wiring ? e.to_wire! : e.total ?? null
  const done = wiring ? Math.min(e.wired ?? 0, total!) : e.processed ?? 0
  return { phase: wiring ? 2 : 1, done, total, pct: total ? Math.floor((100 * done) / total) : 0 }
}

/** Keep the last samples of the phase in flight: a phase change restarts the window, since the two paces differ. */
export function addSample(samples: Sample[], e: Emission, t: number, keep = 10): Sample[] {
  const { phase, done } = phaseOf(e)
  return [...samples.filter(s => s.phase === phase), { t, phase, done }].slice(-keep)
}

/** Time left in the phase in flight at the pace of the last samples. Whole minutes or "under a minute"; before
 *  four samples, or with no progress across them, it says "estimating". Phase 1 cannot know the order's size yet. */
export function timeLeft(samples: Sample[], e: Emission): string {
  const { phase, done, total } = phaseOf(e)
  const s = samples.filter(x => x.phase === phase)
  const first = s[0], last = s[s.length - 1]
  if (!total || s.length < 4 || last.done <= first.done || last.t <= first.t) return "estimating time left"
  const sec = (total - done) / ((last.done - first.done) / ((last.t - first.t) / 1000))
  const left = sec < 60 ? "under a minute" : `about ${Math.round(sec / 60)} min`
  return phase === 1 ? `${left} left on the issues at this pace; the order is timed once it starts` : `${left} left at this pace`
}

/** Measured time, so seconds are honest here. */
export function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000)), m = Math.floor(s / 60)
  return m ? `${m} min ${s % 60} s` : `${s} s`
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
/** The finished run in one sentence, as the production page announces it. */
export function summary(c: RunResult["counts"]): string {
  const x = (k: keyof RunResult["counts"]) => c[k] ?? 0
  return `Shipped ${plural(x("created"), "issue")}` + (x("adopted") ? `, reused ${x("adopted")}` : "") + (x("edited") ? `, edited ${x("edited")}` : "")
    + (x("swept") ? `, closed ${plural(x("swept"), "orphan")}` : "") + ` and declared ${plural(x("wired"), "order")}`
    + (x("skipped") ? `, skipped ${x("skipped")}` : "") + (x("failed") ? `. ${x("failed")} failed` : "")
    + (x("unwired") ? `. ${plural(x("unwired"), "order")} could not be declared` : "") + (x("dead") ? `. ${x("dead")} ${x("dead") === 1 ? "waits" : "wait"} on a dead key` : "") + "."
}

// ---------- the run in flight, shared by every view (the Ship item in the view list shows it everywhere) ----------
export type Run = { e: Emission; samples: Sample[]; started: number; ended?: number }
let run: Run | null = null, timer: ReturnType<typeof setTimeout> | undefined
const subs = new Set<() => void>()
const set = (r: Run | null) => { run = r; subs.forEach(f => f()) }
export const useRun = () => useSyncExternalStore(f => { subs.add(f); return () => { subs.delete(f) } }, () => run)

/** Poll the run every 0.9 s until it is done or failed, as the unit's page does. A poll that throws stops the watch
 *  and keeps the last snapshot: the unit's log is the durable record. `started` lets a run already under way keep
 *  its real start. */
export function watchRun(get: () => Emission | Promise<Emission>, started = Date.now()) {
  clearTimeout(timer)
  set(null)
  const poll = async () => {
    let e: Emission
    try { e = await get() } catch { return }
    const t = Date.now(), prev = run
    set({ e, started: prev?.started ?? started, samples: e.running ? addSample(prev?.samples ?? [], e, t) : prev?.samples ?? [], ended: e.running ? undefined : t })
    if (e.running) timer = setTimeout(poll, POLL_MS)
  }
  void poll()
}
