import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:4174",
    channel: "chrome",
    viewport: { width: 1920, height: 1080 },
  },
  webServer: [
    {
      command:
        "uv run --directory apps/api --cache-dir /private/tmp/low-altitude-uv-cache uvicorn --app-dir src low_altitude_poc_api.app:create_app --factory --host 127.0.0.1 --port 8000",
      cwd: "../..",
      url: "http://127.0.0.1:8000/docs",
      reuseExistingServer: false,
    },
    {
      command: "pnpm --filter @low-altitude/web dev --port 4174",
      cwd: "../..",
      url: "http://127.0.0.1:4174",
      reuseExistingServer: false,
    },
  ],
});
