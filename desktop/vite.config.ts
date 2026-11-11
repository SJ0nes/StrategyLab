/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The built window is served by the local StrategyLab server, from the same
// origin as its API, so no request ever crosses an origin. In development the
// Vite server forwards /api to a server started by hand.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { environment: "jsdom", globals: true, setupFiles: ["tests/setup.ts"] },
});
