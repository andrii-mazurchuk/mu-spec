import { useEffect, useState } from "react"

// Step 6 pages render one spec state at a time from mock fixtures: #/orders?state=empty. Only the review harness
// (review.tsx) reads ?state=; in the shipped product the state comes from the real data, wired by the developer side.
// The hook also marks the document ready after the first paint, so checks never measure a half-rendered page.
export type ScreenState = "default" | "empty" | "loading" | "error" | "stale" | "partial" | "permission" | "offline" | "running"

let review = false
/** Called by review.tsx only: lets ?state= pick the state. */
export const enableReview = () => { review = true }

export function useScreenState(): ScreenState {
  const read = () => (review ? (new URLSearchParams(location.hash.split("?")[1] ?? "").get("state") ?? "default") : "default") as ScreenState
  const [state, setState] = useState(read)
  useEffect(() => {
    const on = () => setState(read())
    addEventListener("hashchange", on)
    requestAnimationFrame(() => document.documentElement.setAttribute("data-ready", ""))
    return () => removeEventListener("hashchange", on)
  }, [])
  return state
}
