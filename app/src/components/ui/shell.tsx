import { useId, useState, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import { Input } from "@/components/ui/input"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"

// The frame every mu-spec view shares (pu's pattern: one top bar, the views in a left outline, a status line first,
// a helper panel on the right). Grey at stage 3: token classes only.

const link = "rounded-sm text-foreground underline underline-offset-4 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"

/** Project picker (reopens the last project) and the fork switcher inside it. */
function ProjectPicker({ projects, project, forks, fork, disabledReason, error, onProject, onFork }: {
  projects: string[]; project: string; forks: string[]; fork: string; disabledReason?: string; error?: string; onProject?: (p: string) => void; onFork?: (f: string) => void
}) {
  const id = useId(), msg = error ?? disabledReason
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label htmlFor={id} className="text-foreground">Project</label>
        <NativeSelect id={id} size="sm" value={project} disabled={!!disabledReason} aria-invalid={error ? true : undefined}
          aria-describedby={msg ? `${id}-msg` : undefined} onChange={e => onProject?.(e.target.value)}>
          {projects.map(p => <NativeSelectOption key={p} value={p}>{p}</NativeSelectOption>)}
        </NativeSelect>
        <span className="text-muted-foreground">fork:</span>
        <DropdownMenu>
          <DropdownMenuTrigger disabled={!!disabledReason} className={cn(link, "disabled:no-underline")} aria-label={`Fork: ${fork}`}>{fork} ▾</DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuRadioGroup value={fork} onValueChange={v => onFork?.(v)}>
              {forks.map(f => <DropdownMenuRadioItem key={f} value={f}>{f}</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {msg && <p id={`${id}-msg`} role={error ? "alert" : undefined} className="m-0 text-xs text-muted-foreground">{msg}</p>}
    </div>
  )
}

/** One search across every view: a spec number or id (S·04, A·04) focuses it on Spine; a word opens Read filtered. */
function SearchBox({ defaultValue = "", disabledReason, error, onSearch }: { defaultValue?: string; disabledReason?: string; error?: string; onSearch?: (q: string) => void }) {
  const id = useId(), [q, setQ] = useState(defaultValue), msg = error ?? disabledReason
  return (
    <form role="search" className="flex w-full flex-col gap-1" onSubmit={e => { e.preventDefault(); if (q.trim()) onSearch?.(q.trim()) }}>
      <Input id={id} type="search" value={q} onChange={e => setQ(e.target.value)} aria-label="Search entries" placeholder="Search S·04, A·04 or a word"
        enterKeyHint="search" autoCapitalize="none" autoCorrect="off" disabled={!!disabledReason}
        aria-invalid={error ? true : undefined} aria-describedby={msg ? `${id}-msg` : undefined} />
      {msg && <p id={`${id}-msg`} role={error ? "alert" : undefined} className="m-0 text-xs text-muted-foreground">{msg}</p>}
    </form>
  )
}

const FRESH = {
  default: (t: string) => `updated ${t}`,
  stale: (t: string) => `updated ${t} · 3 min ago`,
  offline: (t: string) => `offline · last data ${t}, not current`,
}
/** Freshness stamp and refresh, on every view. */
function Freshness({ updated, variant = "default", onRefresh }: { updated: string; variant?: keyof typeof FRESH; onRefresh?: () => void }) {
  return (
    <span className="text-sm whitespace-nowrap text-muted-foreground">
      {FRESH[variant](updated)}
      {variant !== "offline" && <> · <button type="button" className={link} onClick={onRefresh}>refresh</button></>}
    </span>
  )
}

/** The seven views, in the left outline (wrapping rows on phone). `shipping`: a ship run is in flight at that percent
 *  of its current phase; the Ship item carries a small mark and the percent on every view (C43). */
function ViewNav({ views, current, shipping, phase = "issues" }: { views: [string, string][]; current: string; shipping?: number; phase?: "issues" | "order" }) {
  return (
    <nav aria-label="Views" className="flex flex-wrap gap-1 sm:flex-col">
      {views.map(([id, name]) => (
        <a key={id} href={`#/${id}`} aria-label={id === "ship" && shipping !== undefined ? `${name}, shipping, ${phase} ${shipping}%` : undefined} aria-current={id === current ? "page" : undefined}
          className={cn("rounded-md border border-border px-2 py-1 text-sm whitespace-nowrap text-foreground no-underline outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50", id === current && "border-primary border-l-[3px] bg-primary/15 font-semibold hover:bg-primary/15")}>
          {name}
          {id === "ship" && shipping !== undefined && <span className="ml-2 inline-flex items-center gap-1 font-normal text-muted-foreground tabular-nums">
            <span aria-hidden className="size-2 rounded-full bg-primary" />{phase} {shipping}%
          </span>}
        </a>
      ))}
    </nav>
  )
}

/** Slices in the left outline: each opens Read at that slice (or, given onPick, jumps within the page). */
// The slice's Spine colour, so this list is the graph's legend: same order as the graph (sorted names, teal last).
const DOT = ["bg-slice-2", "bg-slice-3", "bg-slice-4", "bg-slice-5", "bg-slice-6", "bg-slice-7", "bg-slice-8", "bg-slice-9", "bg-slice-10", "bg-slice-1"]
function SliceList({ title, slices, hrefFor, onPick }: { title: string; slices: [string, number][]; hrefFor?: (s: string) => string; onPick?: (s: string) => void }) {
  const sorted = slices.map(([s]) => s).sort()
  const name = (s: string) => <span className="flex min-w-0 items-center gap-2 break-words"><span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", DOT[sorted.indexOf(s) % DOT.length])} />{s}</span>
  const item = "flex w-full items-baseline justify-between gap-2 rounded-md border border-border px-2 py-1 text-left text-sm text-foreground no-underline outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50"
  return (
    <div className="flex flex-col gap-1">
      <h2 className="m-0 text-sm font-semibold text-foreground">{title}</h2>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {slices.map(([s, n]) => (
          <li key={s}>
            {onPick
              ? <button type="button" className={item} onClick={() => onPick(s)}>{name(s)}<span className="text-muted-foreground tabular-nums">{n}</span></button>
              : <a className={item} href={hrefFor?.(s) ?? `#/read?slice=${s}`}>{name(s)}<span className="text-muted-foreground tabular-nums">{n}</span></a>}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** tone adds a small status mark beside the count with the node's meaning (J04): warn = changed / needs you, ok = sound,
 *  down = failed. The count itself stays foreground, so it reads at body contrast in both themes. */
export type StatusItem = { n: string; label: string; href?: string; link?: string; tone?: "warn" | "ok" | "down" }
const TONE = { warn: "bg-status-warn", ok: "bg-status-ok", down: "bg-status-down" } as const
const TONE_LABEL = { warn: "needs attention", ok: "sound", down: "failed" } as const
/** One status line per view, counts first. Loading keeps every label and greys each count at its real size. */
function StatusLine({ items, loading = false }: { items: StatusItem[]; loading?: boolean }) {
  return (
    <div role={loading ? "status" : undefined} aria-busy={loading || undefined} aria-label={loading ? "Loading counts" : undefined}
      className="flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded-lg border border-border bg-card px-4 py-2 text-sm text-card-foreground">
      {items.map((x, i) => (
        <span key={i} className="whitespace-nowrap">
          {x.tone && !loading && <span role="img" aria-label={TONE_LABEL[x.tone]} className={cn("mr-1.5 inline-block size-2 rounded-full align-[1px]", TONE[x.tone])} />}
          <b aria-hidden={loading || undefined} className={cn("text-lg tabular-nums", loading && "rounded-sm bg-muted")}>{loading ? <span className="invisible">{x.n}</span> : x.n}</b> {x.label}
          {x.link && x.href && <> · <a className={link} href={x.href}>{x.link}</a></>}
        </span>
      ))}
    </div>
  )
}

/** "Phone shows status only": the phone is information only across holonic units. */
function PhoneNote({ children = "Phone shows status only. Actions are on the desktop view." }: { children?: ReactNode }) {
  return <p className="m-0 text-sm text-muted-foreground">{children}</p>
}

/** The helper panel on the right of most views: the selection, or what the view does. `swapKey` names what it shows
 *  (the selected entry, unit, file or request): when it changes, the new content fades in on --duration-fast while the
 *  panel's frame stays put (J05). The first render and reduced motion show it at once. */
function HelperPanel({ title, children, className, swapKey, ...rest }: { title: ReactNode; children: ReactNode; swapKey?: string } & Omit<React.ComponentProps<"aside">, "title">) {
  const [shown, setShown] = useState(swapKey), [swapped, setSwapped] = useState(false)
  if (shown !== swapKey) { setShown(swapKey); setSwapped(true) }
  return (
    <aside className={cn("flex min-w-0 flex-col rounded-lg border border-border bg-card p-4 text-sm text-card-foreground", className)} {...rest}>
      <div key={swapKey} className={cn("flex min-w-0 flex-col gap-3 transition-opacity duration-(--duration-fast) ease-(--ease-out) motion-reduce:transition-none", swapped && "starting:opacity-0")}>
        <h2 className="m-0 text-base font-semibold break-words">{title}</h2>
        {children}
      </div>
    </aside>
  )
}

export { ProjectPicker, SearchBox, Freshness, ViewNav, SliceList, StatusLine, PhoneNote, HelperPanel }
