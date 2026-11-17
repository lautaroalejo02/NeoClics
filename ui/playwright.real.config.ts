import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

// End-to-end against the REAL muestra-api (not the mock): the production
// build (ui/dist) served by `muestra serve`'s app for a real project that
// tests/integration/e2e_server.py builds with real renders (v001, v002).
// Run with `npm run test:e2e:real` (needs uv on PATH, or MUESTRA_UV).
const PORT = Number(process.env.E2E_REAL_PORT ?? 8791);
const REPO = path.resolve(HERE, "..");
const DIR = path.resolve(HERE, "test-results", "e2e-real");
const UV = process.env.MUESTRA_UV ?? "uv";

export default defineConfig({
  testDir: "e2e-real",
  timeout: 120_000,
  retries: 0,
  workers: 1, // one real project, mutated by the tests in order
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "phone-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `"${UV}" run --directory "${REPO}" python -m tests.integration.e2e_server --dir "${DIR}" --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/project`,
    reuseExistingServer: false,
    timeout: 240_000,
    stdout: "pipe",
    env: { PYTHONUTF8: "1", MUESTRA_E2E_UV: UV },
  },
});
