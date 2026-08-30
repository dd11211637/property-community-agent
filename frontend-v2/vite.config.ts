import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input:
        process.env.VITE_INCLUDE_DEMO === "false"
          ? { app: "index.html" }
          : { app: "index.html", demo: "demo.html" },
    },
  },
  server: {
    port: 5174,
    proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./tests/setup.ts",
    include: ["tests/**/*.test.{ts,tsx}"],
    css: true,
  },
});
