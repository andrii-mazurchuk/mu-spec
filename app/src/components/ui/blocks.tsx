import { useId, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

// Small display and feedback blocks shared by the views. Grey at stage 3: token classes only.

/** Label/value rows (the helper panel's inspector, the file detail). */
function KeyValue({ rows, className }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm", className)}>
      {rows.map(([k, v], i) => <div key={i} className="contents"><dt className="text-muted-foreground">{k}</dt><dd className="m-0 min-w-0 break-words text-foreground">{v}</dd></div>)}
    </dl>
  )
}

/** One step of Ship: what it acts on (or its settings) on the left, counts and its button on the right. `below` spans
 *  the whole step under both (the run in flight), so the two columns keep their place. */
function StepPanel({ title, children, side, below }: { title: ReactNode; children: ReactNode; side: ReactNode; below?: ReactNode }) {
  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4 text-card-foreground">
      <div className="flex flex-col gap-4 sm:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <h2 className="m-0 text-base font-semibold text-foreground">{title}</h2>
          {children}
        </div>
        <div className="flex flex-col gap-2 text-sm text-foreground sm:w-[280px] sm:flex-none">{side}</div>
      </div>
      {below && <div className="border-t border-border pt-4">{below}</div>}
    </div>
  )
}

/** A notice that blocks the next step and asks for a decision (the pending slicing proposal). */
function Callout({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-border bg-muted px-3 py-2 text-sm text-foreground">
      <div className="min-w-0 flex-1">{children}</div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

/** One entry of the corpus in Read. An entry with no body says so instead of disappearing. */
function EntryArticle({ id, title, body, meta }: { id: string; title: string; body?: string; meta: string }) {
  return (
    <article className="flex flex-col gap-1 rounded-md border border-border bg-muted px-4 py-3">
      <h3 className="m-0 text-sm font-semibold break-words text-foreground"><span className="font-mono">{id}</span> · {title}</h3>
      {body ? <p className="m-0 text-sm text-foreground">{body}</p> : <p className="m-0 text-sm text-muted-foreground">no body yet</p>}
      <span className="text-xs text-muted-foreground">{meta}</span>
    </article>
  )
}

const STATE = {
  empty: { role: "status", label: "Empty" },
  loading: { role: "status", label: "Loading" },
  error: { role: "alert", label: "Couldn't load" },
  stale: { role: "status", label: "Stale" },
  partial: { role: "status", label: "Partial" },
  permission: { role: "status", label: "No access" },
  offline: { role: "status", label: "Offline" },
} as const
export type BlockState = keyof typeof STATE
/** What a block says about its state; it lives in the block it affects, never as a page banner. */
function StateBlock({ variant, children, action }: { variant: BlockState; children: ReactNode; action?: ReactNode }) {
  const s = STATE[variant]
  return (
    <div role={s.role} aria-busy={variant === "loading" || undefined} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-dashed border-border px-3 py-2 text-sm text-foreground">
      <p className="m-0 min-w-0 flex-1"><b>{s.label}</b> · {children}</p>
      {action}
    </div>
  )
}

/** Loading: every value becomes a grey bar at its real size; headings, labels and the layout stay. */
function LoadingVeil({ on, children }: { on: boolean; children: ReactNode }) {
  return (
    <div aria-busy={on || undefined} aria-hidden={on || undefined} inert={on || undefined}
      className={cn(on && "[&_a]:no-underline [&_dd]:rounded-sm [&_dd]:bg-muted [&_dd]:text-transparent [&_li]:rounded-sm [&_li]:bg-muted [&_li]:text-transparent [&_p]:rounded-sm [&_p]:bg-muted [&_p]:text-transparent [&_td]:bg-muted [&_td]:text-transparent [&_td_*]:text-transparent [&_text]:fill-transparent [&_[data-value]]:rounded-sm [&_[data-value]]:bg-muted [&_[data-value]]:text-transparent")}>
      {children}
    </div>
  )
}

const RUN = {
  cut: { label: "Cut work units", busy: "Cutting…", variant: "outline" },
  ship: { label: "Ship to GitHub", busy: "Shipping…", variant: "default" },
} as const
export type RunPhase = "idle" | "confirm" | "pending" | "conflict" | "refused"
/** Cut and Ship: the same size (a disabled one reads its reason: "offline", "No GitHub token set") (Ship never smaller, and the primary one). Pending blocks a second press; Ship asks
 *  first with the count it will create; a run already in progress (server 409) and a gate refusal say why. */
function RunButton({ kind, phase = "idle", count, reason, disabledReason, onPress, onConfirm, onCancel }: {
  kind: keyof typeof RUN; phase?: RunPhase; count?: number; reason?: string; disabledReason?: string
  onPress?: () => void; onConfirm?: () => void; onCancel?: () => void
}) {
  const id = useId(), r = RUN[kind], pending = phase === "pending"
  const msg = (phase === "conflict" ? "A run is already in progress; wait for it to finish." : phase === "refused" ? `Refused by a gate: ${reason ?? "the spec is not sound"}.` : undefined)
  if (phase === "confirm") return (
    <div className="flex flex-col gap-2" role="group" aria-labelledby={`${id}-q`}>
      <p id={`${id}-q`} className="m-0 text-sm text-foreground">{kind === "ship" ? `Create ${count ?? 0} issues on GitHub?` : `Record a new cut of ${count ?? 0} units?`}</p>
      <div className="flex gap-2">
        <Button variant={r.variant} size="lg" className="h-12 flex-1 text-base" onClick={onConfirm}>{kind === "ship" ? `Ship ${count ?? 0} issues` : `Cut ${count ?? 0} units`}</Button>
        <Button variant="outline" size="lg" className="h-12" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
  return (
    <div className="flex flex-col gap-1">
      <Button variant={r.variant} size="lg" className={cn("h-12 w-full text-base", kind === "ship" && "border-2 border-primary")}
        disabled={!!disabledReason || phase === "refused" || phase === "conflict"} aria-disabled={pending || undefined} aria-busy={pending || undefined}
        aria-describedby={msg ? `${id}-msg` : undefined} onClick={() => { if (!pending) onPress?.() }}>
        {disabledReason ?? (pending ? r.busy : r.label)}
      </Button>
      {msg && <p id={`${id}-msg`} className="m-0 text-xs text-muted-foreground">{msg}</p>}
    </div>
  )
}

export { KeyValue, StepPanel, Callout, EntryArticle, StateBlock, LoadingVeil, RunButton }
