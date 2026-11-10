import { Collapsible } from "radix-ui"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

// Modules as the developer's file tree (Andrey, round 2-3): folders are visible groups, subfolders nest inside and are
// marked, every folder really folds (Radix Collapsible: height on --duration-fast, J05), rows keep spec and test counts and flags, and a clicked file
// opens in the right panel.
/** `path` is what selection compares when given: two folders may each hold an `__init__.py`. */
export type FileRow = { name: string; spec: number; test: number; slices: number; path?: string }
export type Folder = { name: string; files: FileRow[]; folders: Folder[] }

const isTest = (f: string) => /^test_/.test(f)
// No "no tests" pill: the row's "0 tests" count already says it (Andrey, stage 6).
export function flags(f: FileRow): string[] {
  return [
    f.slices > 1 ? `serves ${f.slices} slices` : "",
    !isTest(f.name) && f.spec === 0 ? "no spec entries" : "",
    isTest(f.name) && f.test === 0 ? "no test entries" : "",
  ].filter(Boolean)
}
export const countFiles = (d: Folder): number => d.files.length + d.folders.reduce((a, x) => a + countFiles(x), 0)

// Each level steps the surface (J09): folder = bordered panel-alt, subfolder = panel fill inside it (no border: a
// bordered box inside a box reads as nested cards), sub-subfolder = ground inside
// that, so the levels read apart before the word "subfolder" is read.
const LEVEL = ["bg-muted", "ms-3 bg-card", "ms-3 bg-background"]

function Group({ folder, depth, selected, onPick, closed }: { folder: Folder; depth: number; selected?: string; onPick?: (f: string) => void; closed: string[] }) {
  // Children render only while open (Radix unmounts closed content after its fold): a folded folder costs nothing
  // (Andrey: views load too much at once). Folding animates the height on --duration-fast; a first render and reduced
  // motion snap.
  return (
    <Collapsible.Root defaultOpen={!closed.includes(folder.name)} className={cn("my-1.5 rounded-md px-2 py-1", depth === 0 && "border border-border", LEVEL[Math.min(depth, LEVEL.length - 1)])}>
      <Collapsible.Trigger className="group/fold flex w-full cursor-pointer items-baseline gap-1 rounded-sm py-0.5 text-left text-sm font-semibold text-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
        <span aria-hidden className="text-muted-foreground group-data-[state=closed]/fold:hidden">▾</span><span aria-hidden className="text-muted-foreground group-data-[state=open]/fold:hidden">▸</span>
        <span className="min-w-0 break-words">{depth > 0 && <span className="font-normal text-muted-foreground">{"subfolder "}</span>}{folder.name} <span className="font-normal text-muted-foreground">{folder.files.length} {folder.files.length === 1 ? "file" : "files"}{folder.folders.length ? " + subfolders" : ""}</span></span>
      </Collapsible.Trigger>
      <Collapsible.Content className="overflow-hidden duration-(--duration-fast) ease-(--ease-out) motion-safe:data-[state=open]:animate-collapsible-down motion-safe:data-[state=closed]:animate-collapsible-up">
      {folder.folders.map(d => <Group key={d.name} folder={d} depth={depth + 1} selected={selected} onPick={onPick} closed={closed} />)}
      <ul className="m-0 list-none p-0">
        {folder.files.map(f => (
          <li key={f.name} className={cn("flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border py-1 ps-2 text-sm last:border-b-0", selected === (f.path ?? f.name) && "rounded-sm border-l-[3px] border-l-primary bg-primary/15")}>
            <button type="button" aria-pressed={selected === (f.path ?? f.name)} onClick={() => onPick?.(f.path ?? f.name)}
              className={cn("min-w-0 rounded-sm text-left break-all text-foreground underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50", selected === (f.path ?? f.name) && "font-semibold underline")}>{f.name}</button>
            <span className="text-muted-foreground tabular-nums whitespace-nowrap">{f.spec} spec · {f.test} {f.test === 1 ? "test" : "tests"}</span>
            {/* "serves N slices" is the rare flag: a filled grey pill; the common gaps stay quiet outlines (J04: all grey). */}
            {flags(f).map(x => /^serves/.test(x)
              ? <Badge key={x} variant="secondary" className="border-border">{x}</Badge>
              : <Badge key={x} variant="outline" className="text-muted-foreground">{x}</Badge>)}
          </li>
        ))}
      </ul>
      </Collapsible.Content>
    </Collapsible.Root>
  )
}

function FileTree({ root, selected, onPick, closed = [] }: { root: Folder; selected?: string; onPick?: (f: string) => void; closed?: string[] }) {
  return (
    <div className="flex flex-col">
      <Group folder={root} depth={0} selected={selected} onPick={onPick} closed={closed} />
    </div>
  )
}

export { FileTree }
