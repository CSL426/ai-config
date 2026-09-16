import { expect, test } from "@playwright/test";
import { boot, calls } from "./mock-bridge";

test("兩個 host 預設未裝，勾選後呼叫 bridge 並顯示 Codex 的信任提示", async ({ page }) => {
  await boot(page);
  await page.locator("#memory-open").click();
  await expect(page.locator("#remember-codex-toggle")).toBeEnabled();
  await expect(page.locator("#remember-codex-toggle")).not.toBeChecked();
  await expect(page.locator("#remember-agy-toggle")).not.toBeChecked();
  await expect(page.locator("#remember-agy-hint")).toContainText("hooks.json");
  await page.evaluate(() => {
    const api = window.pywebview!.api;
    const original = api.memory_info;
    api.memory_info = async (...args) => ({
      ...await original(...args),
      remember_hosts: {
        codex: { available: true, installed: true, version: "0.33.0", trusted: false, detail: "" },
        agy: { available: false, installed: false, version: "", trusted: null, detail: "" },
      },
    });
  });
  await page.locator("#remember-codex-toggle").check();
  await expect.poll(async () => (await calls(page, "set_remember_host")).at(-1)?.args).toEqual(["codex", true]);
  await expect(page.locator("#remember-codex-toggle")).toBeChecked();
  await expect(page.locator("#remember-codex-hint")).toContainText("0.33.0");
  await expect(page.locator("#remember-codex-hint")).toContainText("/hooks");
  // 這台沒有的工具:開關停用、提示說明原因
  await expect(page.locator("#remember-agy-toggle")).toBeDisabled();
  await expect(page.locator("#remember-agy-hint")).toContainText("沒有安裝 Antigravity");
});

test("bridge 失敗時勾選會還原", async ({ page }) => {
  await boot(page);
  await page.locator("#memory-open").click();
  await page.evaluate(() => {
    window.pywebview!.api.set_remember_host = async () => ({ code: 1, output: "找不到 codex", error_code: "FAILED" });
  });
  // 失敗會立刻還原,所以用 click 而不是 check(check 會堅持狀態必須改變)
  await page.locator("#remember-agy-toggle").click();
  await expect(page.locator("#remember-agy-toggle")).not.toBeChecked();
  await expect(page.locator("#memory-feedback")).toContainText("找不到 codex");
});
