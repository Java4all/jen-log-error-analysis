import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API_URL is injected at build time via docker-compose build args.
// In dev mode it proxies to http://api:8000 (docker network) or localhost:8000.
const apiTarget = process.env.VITE_API_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
      "/health": { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  define: {
    // Expose API base URL to runtime code
    __API_BASE__: JSON.stringify(process.env.VITE_API_URL || ""),
  },
});
