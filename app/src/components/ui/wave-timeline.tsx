import { cn } from "@/lib/utils"

// Waves as rows of cards: work units in their build wave (Order) or slices in their writing wave (Waves). A unit card
// selects it into the helper panel; a slice card opens Read at that slice.
export type WaveItem = { id: string; label: string; sub: string; href?: string }
export type Wave = { title: string; items: WaveItem[] }
const VARIANT = {
  units: "select into the helper panel",
  slices: "open Read at the slice",
}

function WaveTimeline({ waves, variant = "units", selected, onPick, more }: { waves: Wave[]; variant?: keyof typeof VARIANT; selected?: string; onPick?: (id: string) => void; more?: string }) {
  const mono = variant === "units" // unit labels are entry ids + a file (J03); slice names are words
  const card = "flex min-w-0 flex-[0_1_260px] flex-col gap-0.5 rounded-md border border-border bg-muted px-2 py-1.5 text-left text-xs text-foreground no-underline outline-none hover:border-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
  return (
    <div className="flex flex-col gap-2" data-variant={variant}>
      {waves.map(w => (
        <div key={w.title} className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3">
          <h3 className="m-0 text-sm font-semibold text-foreground">{w.title}</h3>
          <div className="flex flex-wrap gap-2">
            {w.items.map(it => {
              const inner = <><span data-value="" className={cn("break-words", mono && "font-mono", it.href && "underline underline-offset-4")}>{it.label}</span><span data-value="" className="text-muted-foreground">{it.sub}</span></>
              return variant === "slices"
                ? <a key={it.id} href={it.href} className={card}>{inner}</a>
                : <button key={it.id} type="button" aria-pressed={selected === it.id} onClick={() => onPick?.(it.id)} className={cn(card, selected === it.id && "border-2 border-primary bg-primary/15 font-semibold hover:border-primary")}>{inner}</button>
            })}
          </div>
        </div>
      ))}
      {more && <p className="m-0 text-sm text-muted-foreground">{more}</p>}
    </div>
  )
}

export { WaveTimeline }
