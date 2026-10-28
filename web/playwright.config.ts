import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:13000", headless: true },
  reporter: "list",
});
