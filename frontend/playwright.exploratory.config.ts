import { defineConfig } from "@playwright/test";
import baseConfig from "./playwright.config";

export default defineConfig({
  ...baseConfig,
  testIgnore: [],
  testMatch: "**/exploratory-agent.spec.ts",
});
