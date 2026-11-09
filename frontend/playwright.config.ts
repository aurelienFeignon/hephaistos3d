import { defineConfig } from "@playwright/test";

// Une seule pile partagée : les parcours se succèdent et nettoient ce qu'ils créent.
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  reporter: "list",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  use: { baseURL: "http://127.0.0.1:3031", viewport: { width: 1440, height: 900 } },
  webServer: {
    command: "node e2e/stack.mjs",
    url: "http://127.0.0.1:3031/",
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
    gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
  },
});
