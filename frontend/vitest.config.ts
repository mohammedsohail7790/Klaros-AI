import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const dirname = path.dirname(fileURLToPath(import.meta.url));

// Phase 0 frontend test harness (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md
// §0.7): Vitest + React Testing Library, chosen over Playwright/E2E for
// this "empty but wired" foundation stage — see
// PHASE_0_IMPLEMENTATION_LOG.md for the rationale. `next lint`'s removal
// in Next.js 16 (see log's CI section) is unrelated to this file.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": dirname,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    css: false,
    exclude: ["**/node_modules/**", "**/.next/**"],
  },
});
