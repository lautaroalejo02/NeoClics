import { defineConfig, devices } from "@playwright/test";

// End-to-end: the production build (ui/dist) served by the mock API at the
// same origin, exactly like `muestra serve` will serve it. Run with
// `npm run test:e2e` (builds first).
const PORT = Number(process.env.E2E_PORT ?? 8790);

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: 0,
  workers: 1, // one shared mock server, reset per test
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
    command: "node mock/server.mjs",
    url: `http://127.0.0.1:${PORT}/project`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { PORT: String(PORT), MOCK_DEMO: "0", MOCK_PROJECT_DIR: "test-results/e2e-project" },
  },
});
