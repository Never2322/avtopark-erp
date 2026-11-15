import { readFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// В разработке запросы /api уходят на сервер приложения (порт 8000); в Docker это делает nginx.
const proxy = { "/api": { target: process.env.API_URL || "http://127.0.0.1:8000", changeOrigin: false } };

/** Кладёт в сборку служебный скрипт sw.js со списком собранных файлов: так программа открывается без связи, а после обновления старые файлы удаляются. */
function offlineWorker(): Plugin {
  return {
    name: "erp-offline-worker",
    apply: "build",
    generateBundle(_opts, bundle) {
      const files = Object.keys(bundle).filter((f) => !f.endsWith(".map")).map((f) => "/" + f);
      const source = readFileSync("sw-template.js", "utf8").replace("__BUILD__", Date.now().toString(36)).replace("__FILES__", JSON.stringify(files));
      this.emitFile({ type: "asset", fileName: "sw.js", source });
    },
  };
}

export default defineConfig({
  plugins: [react(), offlineWorker()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  build: { outDir: "dist", sourcemap: false },
});
