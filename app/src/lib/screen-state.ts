import { useEffect } from "react"
import { useStore } from "@/lib/api"

// A view's state comes from the real reads (lib/api), never from the URL:
// - loading: nothing has arrived yet for this project
// - error: the reads failed and there is no earlier data to show
// - offline: the unit could not be reached; the last good data stays readable, marked not current, and nothing posts
// - stale: the last refresh failed (an HTTP error) but earlier data is on screen
// - empty: this view's own data is empty (each view says what that means for it)
// - partial: an optional read failed, so some of what the view draws is missing
// The hook also marks the document ready after the first paint, so checks never measure a half-rendered page.
export type ScreenState = "default" | "empty" | "loading" | "error" | "stale" | "partial" | "permission" | "offline" | "running"

export function useScreenState(empty = false): ScreenState {
  const s = useStore()
  useEffect(() => { requestAnimationFrame(() => document.documentElement.setAttribute("data-ready", "")) }, [])
  if (s.projects && !s.projects.length) return s.error ? (s.offline ? "offline" : "error") : "empty"
  if (!s.data) return s.error ? "error" : "loading"
  if (s.error) return s.offline ? "offline" : "stale"
  if (empty) return "empty"
  if (s.failed.length) return "partial"
  return "default"
}
