import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// One self-contained HTML file: the artifact host serves a single page,
// so every script and style is inlined at build time.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  // The hub reads ../features.txt (the team's tracker) with ?raw.
  server: { fs: { allow: [".."] } },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
