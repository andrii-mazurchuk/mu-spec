import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { App, useRoute } from "./app"

// The shipped entry (index.html): the pages only. The review harness (gallery, page index, ?state= previews) is
// review.html → review.tsx and is never part of the product.
function Root() {
  return <App route={useRoute()} />
}

createRoot(document.getElementById("root")!).render(<StrictMode><Root /></StrictMode>)
