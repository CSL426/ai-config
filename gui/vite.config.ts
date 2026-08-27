import { defineConfig } from "vite";

export default defineConfig({
  // pywebview 以 file:// 載入,資源路徑必須是相對的
  base: "./",
  build: {
    outDir: "../ai_config/gui_assets",
    emptyOutDir: true,
  },
});
