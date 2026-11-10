import type { ComponentType } from "react"
import { screens } from "./screens"

// The seven pages, keyed by spec screen id without the "SCR-" prefix: "spine" → #/spine. Pages import only approved
// components from "@/components/ui/*", read the unit through "@/lib/api", take their state from useScreenState()
// (the real reads), and keep the chosen wireframe's data-block names on their blocks.
export const pages: Record<string, ComponentType> = screens
