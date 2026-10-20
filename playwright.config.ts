import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";

const testDatabase = join(tmpdir(), `undercover-e2e-${randomUUID()}.sqlite3`);
const requestedChannel = process.env.UNDERCOVER_PLAYWRIGHT_CHANNEL;
const systemChannel =
  requestedChannel === "chrome" || requestedChannel === "msedge"
    ? requestedChannel
    : undefined;

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5181",
    ...(systemChannel === undefined ? {} : { channel: systemChannel }),
  },
  webServer: [
    {
      command: "uv run -m uvicorn backend.main:app --port 8001",
      url: "http://127.0.0.1:8001/api/health",
      reuseExistingServer: false,
      env: {
        ...process.env,
        UNDERCOVER_DB_PATH: testDatabase,
        UNDERCOVER_AGENT_TEST_MODE: "1",
        UNDERCOVER_TEST_FAILURE_PLAN: "CLM-1006:always,CLM-1007:once",
        UNDERCOVER_TEST_MISSED_CLAIM: "CLM-1002",
        UNDERCOVER_BACKEND_URL: "http://127.0.0.1:8001",
      },
    },
    {
      command: "npx vite dashboard --host 127.0.0.1 --port 5181 --strictPort",
      url: "http://127.0.0.1:5181",
      reuseExistingServer: false,
      env: {
        ...process.env,
        UNDERCOVER_API_URL: "http://127.0.0.1:8001",
      },
    },
  ],
});
