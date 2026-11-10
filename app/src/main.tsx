import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { App, useRoute } from "./app"
import { startPolling } from "./lib/api"

// Framed under the node, the node owns the theme (it injects the palette and the .dark class). Standalone, follow the
// reader's system setting.
if (window.self === window.top && matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.classList.add("dark")

function Root() {
  return <App route={useRoute()} />
}

startPolling()
createRoot(document.getElementById("root")!).render(<StrictMode><Root /></StrictMode>)
