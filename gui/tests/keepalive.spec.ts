import { expect, test, type Page } from "@playwright/test";
import { boot, calls, queue } from "./mock-bridge";

const success = { code: 0, output: "完成", error: null };
const active = {
  installed: true, times: ["06:30", "11:35"], model: "test-model",
  ccs: "", recent: ["first run", "latest run"],
};

async function openMemory(page: Page, state: object | null = null) {
  await boot(page);
  await page.evaluate((state) => {
    const bridge = window.pywebview!.api;
    const original = bridge.memory_info;
    bridge.memory_info = async () => {
      const info = await original();
      if (state === null) delete (info as Partial<typeof info>).keepalive;
      else info.keepalive = state as typeof info.keepalive;
      return info;
    };
  }, state);
  await page.locator("#memory-open").click();
}

test("舊版狀態仍能開啟記憶頁，未啟用時隱藏時間", async ({ page }) => {
  await openMemory(page);
  await expect(page.locator("#keepalive-toggle")).not.toBeChecked();
  await expect(page.locator("#keepalive-times-row")).toBeHidden();
  await expect(page.locator("#memory-summary")).not.toBeEmpty();
});

test("呈現已啟用時間與最新紀錄", async ({ page }) => {
  await openMemory(page, active);
  await expect(page.locator("#keepalive-toggle")).toBeChecked();
  await expect(page.locator("#keepalive-times")).toHaveValue("06:30 11:35");
  await expect(page.locator("#keepalive-recent")).toHaveText("最近：latest run");
});

test("啟用後重新讀取狀態", async ({ page }) => {
  await boot(page);
  await page.locator("#memory-open").click();
  const info = await page.evaluate(() => window.pywebview!.api.memory_info());
  await queue(page, "memory_info", { ...info, keepalive: active });
  await page.locator("#keepalive-toggle").check();
  await expect.poll(async () => (await calls(page, "set_keepalive")).at(-1)?.args).toEqual([true]);
  await expect(page.locator("#keepalive-times")).toHaveValue("06:30 11:35");
});

test("停用與時間更新傳送正確參數", async ({ page }) => {
  await openMemory(page, active);
  await page.locator("#keepalive-times").fill("07:00, 12:05  17:10");
  await page.locator("#keepalive-save").click();
  await expect.poll(async () => (await calls(page, "set_keepalive")).at(-1)?.args)
    .toEqual([true, ["07:00", "12:05", "17:10"]]);
  await expect(page.locator("#keepalive-save")).toBeEnabled();
  await page.locator("#keepalive-toggle").click();
  await expect.poll(async () => (await calls(page, "set_keepalive")).at(-1)?.args).toEqual([false]);
});

test("既有 ccs 排程會提示，拒絕啟用時恢復開關", async ({ page }) => {
  await openMemory(page, { ...active, installed: false, ccs: "crontab" });
  await expect(page.locator("#keepalive-hint")).toContainText("claude-scheduler");
  await queue(page, "set_keepalive", { ...success, code: 1, output: "請先移除 ccs 排程" });
  await page.locator("#keepalive-toggle").click();
  await expect(page.locator("#keepalive-toggle")).not.toBeChecked();
  await expect(page.locator("#memory-feedback")).toContainText("請先移除 ccs 排程");
});

test("時間被拒絕時保留輸入供修正", async ({ page }) => {
  await openMemory(page, active);
  await queue(page, "set_keepalive", { ...success, code: 1, output: "格式是 HH:MM" });
  await page.locator("#keepalive-times").fill("25:00");
  await page.locator("#keepalive-save").click();
  await expect(page.locator("#memory-feedback")).toContainText("格式是 HH:MM");
  await expect(page.locator("#keepalive-times")).toHaveValue("25:00");
  await expect(page.locator("#keepalive-save")).toBeEnabled();
});

test("bridge 例外會顯示錯誤並恢復控制項", async ({ page }) => {
  await openMemory(page, active);
  await queue(page, "set_keepalive", { reject: "connection lost" }, { reject: "connection lost" });
  await page.locator("#keepalive-toggle").click();
  await expect(page.locator("#keepalive-toggle")).toBeChecked();
  await expect(page.locator("#keepalive-toggle")).toBeEnabled();
  await expect(page.locator("#memory-feedback")).toContainText("connection lost");
  await page.locator("#keepalive-save").click();
  await expect.poll(async () => (await calls(page, "set_keepalive")).length).toBe(2);
  await expect(page.locator("#keepalive-save")).toBeEnabled();
  await expect(page.locator("#memory-feedback")).toContainText("connection lost");
});

test("顯示實際視窗、偏離排程的警告與各帳號結果", async ({ page }) => {
  await openMemory(page, {
    ...active,
    window: { start: "09:50", reset: "14:50", drift: "07:00" },
    tools: {
      claude: { installed: true, times: ["07:00"], recent: [], accounts: {} },
      codex: {
        installed: true, times: ["07:00"], recent: [],
        accounts: { ".codex-set": "exit 1: ERROR: usage limit", ".codex-csl": "exit 0: hi" },
      },
    },
  });
  await expect(page.locator("#keepalive-window")).toHaveText("目前視窗 09:50–14:50");
  await expect(page.locator("#keepalive-drift")).toContainText("不是從排程的 07:00 開始");
  await expect(page.locator("#keepalive-accounts li")).toHaveCount(2);
  await expect(page.locator("#keepalive-accounts")).toContainText("codex .codex-csl：exit 0: hi");
});

test("視窗準時開始就不警告", async ({ page }) => {
  await openMemory(page, { ...active, window: { start: "07:00", reset: "12:00", drift: "" } });
  await expect(page.locator("#keepalive-window")).toHaveText("目前視窗 07:00–12:00");
  await expect(page.locator("#keepalive-drift")).toBeEmpty();
});
