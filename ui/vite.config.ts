import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In production the API serves ui/dist at the same origin, so no proxy is
// needed. In dev, MUESTRA_API (default http://localhost:8777) is proxied so
// the app always talks same-origin, exactly like production.
const apiTarget = process.env.MUESTRA_API ?? "http://localhost:8777";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/tools": apiTarget,
      "/files": apiTarget,
      "/jobs": apiTarget,
      "/events": { target: apiTarget, ws: true },
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    include: ["test/**/*.test.{ts,tsx}"],
    testTimeout: 20000,
  },
});
