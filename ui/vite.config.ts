import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// In production the API serves ui/dist at the same origin, so the app
// always talks to "" (same origin). In dev, MUESTRA_API (default: the mock
// on http://localhost:8777) is proxied so dev also talks same-origin.
const apiTarget = process.env.MUESTRA_API ?? "http://localhost:8777";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/tools": apiTarget,
      "/files": apiTarget,
      "/jobs": apiTarget,
      "/project": apiTarget,
      "/waveform": apiTarget,
      "/events": { target: apiTarget.replace(/^http/, "ws"), ws: true },
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
    chunkSizeWarningLimit: 400,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    include: ["test/**/*.test.{ts,tsx}"],
    testTimeout: 20000,
  },
});
