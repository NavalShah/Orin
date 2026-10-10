import { defineConfig } from "@playwright/test";

// End-to-end smoke tests for the three screens (spec 13.7). They run the real API in
// offline mode, so no keys are needed. Set ORIN_PYTHON to the interpreter of your
// Python environment if `python` on PATH is not it.
const python = process.env.ORIN_PYTHON ?? "python";
const dataDir = process.env.ORIN_E2E_DATA_DIR ?? "./data/e2e";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: [
    {
      command: `${python} -m uvicorn server.main:app --port 8000`,
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: true,
      timeout: 120_000,
      env: { ORIN_OFFLINE: "1", ORIN_DATA_DIR: dataDir, ORIN_APP_ORIGIN: "http://localhost:3000" },
    },
    {
      command: "npm run dev",
      url: "http://localhost:3000/studio",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
