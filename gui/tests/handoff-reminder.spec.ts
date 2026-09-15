import { expect, test } from "@playwright/test";
import { boot, calls, queue } from "./mock-bridge";

async function openMemory(page: Parameters<typeof boot>[0]) {
  await page.locator("#memory-open").click();
  await expect(page.locator("#handoff-reminder-toggle")).toBeEnabled();
}

test("提醒預設停用，說明 Claude Code 範圍並可設定門檻後啟用", async ({ page }) => {
  await boot(page);
  await openMemory(page);
  await expect(page.locator("#handoff-reminder-toggle")).not.toBeChecked();
  await expect(page.locator("#handoff-reminder-threshold")).toHaveValue("70");
  await expect(page.locator("#handoff-reminder-save")).toBeDisabled();
  await expect(page.locator("#memory-panel")).toContainText("僅適用 Claude Code；只提醒，不會自動寫交接");
  await page.evaluate(() => {
    const api = window.pywebview!.api;
    const original = api.memory_info;
    api.memory_info = async (...args) => ({
      ...await original(...args),
      handoff_reminder: { enabled: true, threshold: 80, installed: true },
    });
  });
  await page.locator("#handoff-reminder-threshold").fill("80");
  await page.locator("#handoff-reminder-toggle").check();
  await expect.poll(async () => (await calls(page, "set_handoff_reminder")).at(-1)?.args).toEqual([true, 80]);
  await expect(page.locator("#handoff-reminder-hint")).toContainText("80%");
});

test("不接受超出範圍或小數門檻", async ({ page }) => {
  await boot(page);
  await openMemory(page);
  for (const value of ["0", "100", "70.5", ""]) {
    await page.locator("#handoff-reminder-threshold").fill(value);
    await page.locator("#handoff-reminder-toggle").click();
    await expect(page.locator("#handoff-reminder-toggle")).not.toBeChecked();
  }
  expect(await calls(page, "set_handoff_reminder")).toHaveLength(0);
});

test("設定失敗或 bridge 中斷會還原開關並顯示錯誤", async ({ page }) => {
  await boot(page);
  await openMemory(page);
  for (const reply of [
    { code: 1, output: "另一個動作正在執行", error: "BUSY" },
    { reject: "連線中斷" },
  ]) {
    await queue(page, "set_handoff_reminder", reply);
    await page.locator("#handoff-reminder-toggle").click();
    await expect(page.locator("#handoff-reminder-toggle")).not.toBeChecked();
    await expect(page.locator("#memory-feedback")).toContainText("output" in reply ? reply.output : reply.reject);
    await expect(page.locator("#handoff-reminder-toggle")).toBeEnabled();
  }
});

test("已啟用可更新門檻與停用，操作中鎖住其他控制項", async ({ page }) => {
  await boot(page);
  await page.evaluate(() => {
    const api = window.pywebview!.api;
    const original = api.memory_info;
    api.memory_info = async (...args) => ({
      ...await original(...args),
      handoff_reminder: { enabled: true, threshold: 75, installed: true },
    });
    const configure = api.set_handoff_reminder;
    api.set_handoff_reminder = async (...args) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      return configure(...args);
    };
  });
  await openMemory(page);
  await expect(page.locator("#handoff-reminder-toggle")).toBeChecked();
  await expect(page.locator("#handoff-reminder-threshold")).toHaveValue("75");
  await page.locator("#handoff-reminder-threshold").fill("85");
  await page.locator("#handoff-reminder-save").click();
  await expect(page.locator("#memory-refresh")).toBeDisabled();
  await expect(page.locator("#handoff-reminder-toggle")).toBeDisabled();
  await expect.poll(async () => (await calls(page, "set_handoff_reminder")).at(-1)?.args).toEqual([true, 85]);
  await expect(page.locator("#handoff-reminder-toggle")).toBeEnabled();
  await page.locator("#handoff-reminder-toggle").uncheck();
  await expect.poll(async () => (await calls(page, "set_handoff_reminder")).at(-1)?.args).toEqual([false, 75]);
});
