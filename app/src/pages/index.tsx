import type { ComponentType } from "react"
import { screens } from "./screens"

// Step 6 pages, keyed by spec screen id without the "SCR-" prefix: "orders" → #/orders.
// Pages import only approved components from "@/components/ui/*", render from mock fixtures in
// "@/fixtures/<screen>", call useScreenState() from "@/lib/screen-state" to render ?state=…, and keep the
// chosen wireframe's data-block names on their blocks.
export const pages: Record<string, ComponentType> = screens
