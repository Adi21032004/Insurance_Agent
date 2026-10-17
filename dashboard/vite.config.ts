import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const apiUrl = process.env.UNDERCOVER_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": apiUrl } },
  test: {
    environment: "jsdom",
    pool: "threads",
    maxWorkers: 1,
    coverage: {
      include: ["src/**"],
      exclude: ["src/main.tsx"],
      thresholds: { statements: 80 },
    },
  },
});
