import { fileURLToPath } from "node:url"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig, type Plugin } from "vite"
import { viteSingleFile } from "vite-plugin-singlefile"

// The product ships as ONE self-contained file: JS, CSS and fonts inlined, written to mu_spec/dashboard.html and
// committed (UNIT_CONTRACT.md, "The dashboard"). CI rebuilds it and fails if the committed file differs, so the build
// must be deterministic. base "./" + hash routes: every request the page makes stays relative to where it is served.
// The review harness never came into this repo, so the build is the product alone (SHIP_ONLY=1 in the source project).

/** Fonts as woff2 only: every browser the console supports reads it, and the woff fallbacks doubled the page. */
const woff2Only = (): Plugin => ({
  name: "mu-spec:woff2-only",
  enforce: "post",
  generateBundle(_, bundle) {
    for (const f of Object.values(bundle))
      if (f.type === "asset" && f.fileName.endsWith(".css") && typeof f.source === "string")
        f.source = f.source.replace(/,\s*url\(data:font\/woff;base64,[^)]*\)\s*format\(["']woff["']\)/g, "")
  },
})

/** The page carries no URL a browser could follow off this origin (tests/test_dashboard.py). Two strings in the
 *  dependencies are text, not resources: React's error-decoder link and Tailwind's licence banner. They lose their
 *  scheme and keep their words; anything else that looks like a URL fails the build rather than shipping. */
const noExternalUrls = (): Plugin => ({
  name: "mu-spec:no-external-urls",
  enforce: "post",
  generateBundle(_, bundle) {
    for (const f of Object.values(bundle)) {
      const text = f.type === "chunk" ? f.code : typeof f.source === "string" ? f.source : null
      if (text === null) continue
      const out = text.replaceAll("https://react.dev/errors/", "react.dev/errors/").replaceAll("https://tailwindcss.com", "tailwindcss.com")
      const left = out.match(/https?:\/\/(?!www\.w3\.org)[^\s"'`)]*/g)
      if (left) this.error(`${f.fileName} carries an external URL: ${[...new Set(left)].join(", ")}`)
      if (f.type === "chunk") f.code = out
      else f.source = out
    }
  },
})

export default defineConfig({
  base: "./",
  publicDir: false, // nothing ships beside the page
  build: {
    outDir: "../mu_spec",
    emptyOutDir: false, // the output dir is the Python package: only dashboard.html is ours
    modulePreload: { polyfill: false }, // one inline module: nothing to preload, and the polyfill fetches
    rollupOptions: { input: "dashboard.html" },
  },
  plugins: [woff2Only(), react(), tailwindcss(), noExternalUrls(), viteSingleFile({ removeViteModuleLoader: true })],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
})
