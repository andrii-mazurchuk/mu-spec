import { useEffect, useState } from "react"
import { pages } from "./pages"

// The product: hash routes to the pages and nothing else. Hash routes stay inside the page, so it never navigates
// the frame the node puts it in.
export function useRoute() {
  // The whole hash, not just the route: #/task?id=a → #/task?id=b is the same route and must still re-render.
  const [hash, setHash] = useState(() => location.hash)
  useEffect(() => {
    const on = () => setHash(location.hash)
    addEventListener("hashchange", on)
    return () => removeEventListener("hashchange", on)
  }, [])
  return hash.replace(/^#\/?/, "").split("?")[0]
}

/** #/<page-id>; an empty or unknown route opens the first page, so the product never shows an index of its own. */
export function App({ route }: { route: string }) {
  const Page = pages[route] ?? Object.values(pages)[0]
  return Page ? <Page /> : null
}
