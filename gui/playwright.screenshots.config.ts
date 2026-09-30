import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// README 的截圖:跟一般測試分開跑(pnpm screenshots),輸出到 docs/screenshots/
export default defineConfig({
  ...base,
  testDir: "./screenshots",
  fullyParallel: false,
  use: { ...base.use, viewport: { width: 960, height: 720 }, colorScheme: "light", timezoneId: "Asia/Taipei" },
});
