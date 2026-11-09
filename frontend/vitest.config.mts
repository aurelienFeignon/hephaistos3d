import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "server-only": fileURLToPath(new URL("./tests/server-only.ts", import.meta.url)) },
  },
  // Node par défaut ; les tests de composants déclarent `@vitest-environment jsdom`.
  test: { environment: "node", include: ["src/**/*.test.{ts,tsx}"] },
});
