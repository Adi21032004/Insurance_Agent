import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";

const testDatabase = join(tmpdir(), `undercover-e2e-${randomUUID()}.sqlite3`);
const requestedChannel = process.env.UNDERCOVER_PLAYWRIGHT_CHANNEL;
const portOffset = Number(process.env.UNDERCOVER_E2E_PORT_OFFSET ?? "25000");
const backendPort = String(8001 + portOffset);
const agentPort = String(8002 + portOffset);
const standInPort = String(8003 + portOffset);
const dashboardPort = String(5181 + portOffset);
const systemChannel =
  requestedChannel === "chrome" || requestedChannel === "msedge"
    ? requestedChannel
    : undefined;

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${dashboardPort}`,
    ...(systemChannel === undefined ? {} : { channel: systemChannel }),
  },
  webServer: [
    {
      command: `uv run -m uvicorn backend.main:app --port ${backendPort}`,
      url: `http://127.0.0.1:${backendPort}/api/health`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        DATABASE_URL: "",
        OPENROUTER_API_KEY: "",
        LANGFUSE_PUBLIC_KEY: "",
        LANGFUSE_SECRET_KEY: "",
        SSLKEYLOGFILE: "",
        UNDERCOVER_DB: testDatabase,
        UNDERCOVER_AGENT: `http://127.0.0.1:${standInPort}`,
        UNDERCOVER_AGENT_TEST_MODE: "1",
        UNDERCOVER_TEST_FAILURE_PLAN: "CLM-1006:always,CLM-1007:once",
        UNDERCOVER_TEST_MISSED_CLAIM: "CLM-1002",
        UNDERCOVER_BACKEND_URL: `http://127.0.0.1:${backendPort}`,
      },
    },
    {
      command: `uv run -m uvicorn agent.service:app --host 127.0.0.1 --port ${agentPort}`,
      url: `http://127.0.0.1:${agentPort}/api/health`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        DATABASE_URL: "",
        LANGFUSE_PUBLIC_KEY: "",
        LANGFUSE_SECRET_KEY: "",
        OPENROUTER_API_KEY: "",
        SSLKEYLOGFILE: "",
        UNDERCOVER_AGENT_TEST_MODE: "1",
        UNDERCOVER_BACKEND_URL: `http://127.0.0.1:${backendPort}`,
        UNDERCOVER_TEST_FAILURE_PLAN: "CLM-1006:always,CLM-1007:once",
        UNDERCOVER_TEST_MISSED_CLAIM: "CLM-1002",
      },
    },
    {
      command: "node e2e/agent_stand_in.ts",
      url: `http://127.0.0.1:${standInPort}/api/health`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        AGENT_STAND_IN_PORT: standInPort,
        AGENT_STAND_IN_UPSTREAM_PORT: agentPort,
      },
    },
    {
      command: `npx vite dashboard --host 127.0.0.1 --port ${dashboardPort} --strictPort`,
      url: `http://127.0.0.1:${dashboardPort}`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        UNDERCOVER_API_URL: `http://127.0.0.1:${backendPort}`,
      },
    },
  ],
});
