import { useId } from "react"
import { CheckIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { elapsed, phaseOf, summary, timeLeft, type Emission, type RunResult, type Sample, type Trouble } from "@/lib/run"

// A ship run as it happens (S31): two phases as a small stepper, the phase in flight as a determinate bar, live
// counts, time so far and a pace-based time left. On finish: the summary (role=status) and every unit that did not
// land, with its reason (the production page's shipTrouble categories). Information only: no actions, so the phone
// shows it whole.

const PHASES = [["Creating issues", "work units"], ["Declaring order", "dependencies"]] as const
type Step = "done" | "current" | "stopped" | "todo"
// [kind, title for one, title for several, why, tone]
const TROUBLE: [keyof RunResult, string, string, string, "down" | "warn"][] = [
  ["failed", "did not ship: no issue exists", "did not ship: no issue exists", "Nobody will be handed this work. Shipping again ships only these.", "down"],
  ["unwired", "order not declared", "orders not declared", "These issues exist, but the order between them does not, so the tracker releases the blocked work too early.", "down"],
  ["dead", "waits on a dead key", "wait on a dead key", "These open issues wait on a key no live issue carries; they will never start.", "down"],
  ["raced", "written while being picked up", "written while being picked up", "The consumer took these between this run's read and its write; the agent may have started from the earlier text.", "warn"],
  ["touched", "changed here but picked up there: left alone", "changed here but picked up there: left alone", "Never edited after pickup. To apply the change, release the unit and ship again.", "warn"],
]
const DOT = { down: "bg-status-down", warn: "bg-status-warn" }

function Mark({ step, n }: { step: Step; n?: number }) {
  return (
    <span aria-hidden className={cn("inline-flex size-5 shrink-0 items-center justify-center rounded-full border text-xs tabular-nums",
      step === "current" ? "border-primary text-foreground" : step === "done" ? "border-status-ok text-status-ok" : step === "stopped" ? "border-status-down bg-status-down" : "border-border text-muted-foreground")}>
      {step === "done" ? <CheckIcon className="size-3.5" /> : step === "stopped" ? null : n}
    </span>
  )
}

function TroubleList({ kind, items, title, why, tone }: { kind: string; items: Trouble[]; title: string; why: string; tone: "down" | "warn" }) {
  return (
    <section aria-label={`${items.length} ${title}`} data-trouble={kind} className="flex min-w-0 flex-col gap-2 rounded-md border border-border px-3 py-2">
      <h3 className="m-0 text-sm font-semibold text-foreground"><span aria-hidden className={cn("mr-1.5 inline-block size-2 rounded-full align-[1px]", DOT[tone])} /><span className="tabular-nums">{items.length}</span> {title}</h3>
      <p className="m-0 text-sm text-muted-foreground">{why}</p>
      <ul className="m-0 flex list-none flex-col p-0 text-sm">
        {items.map((t, i) => (
          <li key={i} className="flex min-w-0 flex-col gap-0.5 border-t border-border py-2">
            <span className="font-mono break-words text-foreground">{t.key ?? t.blocked}{t.number ? <span className="font-sans text-muted-foreground"> · #{t.number}</span> : null}</span>
            {t.blocker && <span className="break-words text-muted-foreground">waits on <span className="font-mono text-foreground">{t.blocker}</span></span>}
            <span className="break-words text-foreground">{t.reason}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** `samples` are the recent polls of the phase in flight (lib/run addSample); `elapsedMs` is time since the press. */
function RunProgress({ emission: e, elapsedMs, samples = [] }: { emission: Emission; elapsedMs: number; samples?: Sample[] }) {
  const id = useId()
  const { phase, done, total, pct } = phaseOf(e)
  const finished = e.phase === "done", stopped = e.phase === "failed", inFlight = !finished && !stopped
  const step = (n: 1 | 2): Step => (finished || n < phase ? "done" : n > phase ? "todo" : stopped ? "stopped" : "current")
  const [name, unit] = PHASES[phase - 1]
  const line = total == null ? `${name} · counting work units` : `${name} · ${done} of ${total} ${unit}${phase === 2 ? " · one GitHub call each" : ""} · ${pct}%`
  const counts: [string, number | undefined, ("down" | "warn")?][] = [["created", e.created], ["reused", e.adopted], ["edited", e.edited], ["orders", e.wired], ["orphans closed", e.swept],
    ...((e.failed ?? 0) > 0 ? [["failed", e.failed, "down"]] as typeof counts : []), ...((e.skipped ?? 0) > 0 ? [["skipped", e.skipped, "warn"]] as typeof counts : [])]
  const r = e.result
  return (
    <div className="flex min-w-0 flex-col gap-3 text-sm text-foreground">
      <ol aria-label="Ship run" className="m-0 flex list-none flex-wrap items-center gap-x-6 gap-y-2 p-0">
        {([1, 2] as const).map(n => (
          <li key={n} aria-current={step(n) === "current" ? "step" : undefined} className={cn("flex items-center gap-2", step(n) === "todo" ? "text-muted-foreground" : "font-semibold")}>
            <Mark step={step(n)} n={n} />{PHASES[n - 1][0]}
            {step(n) === "done" && <span className="font-normal text-muted-foreground tabular-nums">· {n === 1 ? `${e.total} units` : `${e.wired} orders`}</span>}
            {step(n) === "stopped" && <span className="font-normal text-muted-foreground">· stopped</span>}
          </li>
        ))}
        <li className={cn("flex items-center gap-2", finished ? "font-semibold" : "text-muted-foreground")}><Mark step={finished ? "done" : "todo"} />Done</li>
      </ol>
      {!finished && <>
        <div role="progressbar" aria-label={name} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={line}
          aria-invalid={stopped || undefined} aria-describedby={stopped ? `${id}-err` : undefined} className="h-2.5 overflow-hidden rounded-full border border-border bg-muted">
          {/* keyed by phase: a phase change swaps the bar at once; within a phase the width eases between polls (J05) */}
          <div key={phase} className={cn("h-full rounded-full transition-[width] duration-(--duration-base) ease-(--ease-out) motion-reduce:transition-none", stopped ? "bg-status-down" : "bg-primary")} style={{ width: `${pct}%` }} />
        </div>
        <p className="m-0 tabular-nums"><b>{name}</b>{line.slice(name.length)}</p>
      </>}
      <ul aria-label="Counts so far" className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0">
        {counts.map(([k, n, tone]) => (
          <li key={k} className="whitespace-nowrap">{tone && <span aria-hidden className={cn("mr-1.5 inline-block size-2 rounded-full align-[1px]", DOT[tone])} />}{k} <b className="tabular-nums">{n ?? 0}</b></li>
        ))}
      </ul>
      <p className="m-0 text-muted-foreground tabular-nums">{inFlight ? `${elapsed(elapsedMs)} so far · ${timeLeft(samples, e)}` : `took ${elapsed(elapsedMs)}`}</p>
      {stopped && <p id={`${id}-err`} role="alert" className="m-0">The run stopped at {done} of {total ?? "?"} {unit}: {e.error ?? "the unit gave no reason"}. {e.created ?? 0} issues were created before it; shipping again reuses them and ships only the rest.</p>}
      {/* Always mounted, so the finish is announced when its text arrives. */}
      <p role="status" className={cn("m-0", !finished && "sr-only")}>{finished ? (r ? summary(r.counts) : "The run finished with no counts recorded.") : ""}</p>
      {r && TROUBLE.map(([k, one, many, why, tone]) => { const items = r[k] as Trouble[] | undefined; return items?.length ? <TroubleList key={k} kind={k} items={items} title={items.length === 1 ? one : many} why={why} tone={tone} /> : null })}
    </div>
  )
}

export { RunProgress }
