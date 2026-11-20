import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build goes where the Python server serves it from; `npm run dev` proxies the API to it.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "../rca/ui/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
