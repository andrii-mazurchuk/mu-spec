import { fileURLToPath } from "node:url"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"

// base "./" + hash routes: the built app opens from any static server or folder. Two entries: index.html is the
// product (what ships); review.html is the pipeline's review harness. SHIP_ONLY=1 builds the product alone, which is
// how page-check proves the harness never reaches it.
const input = { index: "index.html", ...(process.env.SHIP_ONLY ? {} : { review: "review.html" }) }
export default defineConfig({
  base: "./",
  build: { rollupOptions: { input } },
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { fs: { allow: [".."] } }, // tokens.css lives in ../04-design
})
