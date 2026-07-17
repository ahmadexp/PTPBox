import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  root: ".",
  plugins: [react()],
  publicDir: "public",
  build: {
    outDir: "dist-standalone",
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(import.meta.dirname, "standalone/index.html"),
    },
  },
});
