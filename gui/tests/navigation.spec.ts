import { expect, test } from "@playwright/test";
import { boot, calls } from "./mock-bridge";

test("Esc 返回記憶、技能、匯出與套用，焦點回到入口", async ({ page }) => {
  await boot(page);
  await page.locator("#memory-open").click();
  await expect(page.locator("#memory-status")).toHaveText("共用位置已連結");
  await page.keyboard.press("Escape");
  await expect(page.locator("#memory-open")).toBeFocused();
  await page.locator("#package-open").click();
  await page.locator("#skill-list input").first().check();
  await page.locator("#skill-package").click();
  await expect(page.locator("#package-result")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("#skill-package")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#package-open")).toBeFocused();
  await page.locator("[data-cmd=apply]").click();
  await page.locator("#category-settings").check();
  await page.keyboard.press("Escape");
  await expect(page.locator("[data-cmd=apply]")).toBeFocused();
  await page.locator("[data-cmd=apply]").click();
  await expect(page.locator("#category-settings")).not.toBeChecked();
});

test("Esc 先關閉設定、取消預覽必須撤銷 token，每次只返回一層", async ({ page }) => {
  await boot(page);
  await page.locator("#memory-open").click();
  await page.locator("#settings-open").click();
  await page.keyboard.press("Escape");
  await expect(page.locator("#settings")).toBeHidden();
  await expect(page.locator("#memory-panel")).toBeVisible();
  await page.locator("[data-memory-action=enable]").click();
  await expect(page.locator("#confirm")).toBeVisible();
  // Cancellation works even when focus is outside the confirmation box.
  await page.locator("#output-title").focus();
  await page.keyboard.press("Escape");
  await expect(page.locator("[data-memory-action=enable]")).toBeFocused();
  await expect(page.locator("#memory-panel")).toBeVisible();
  expect((await calls(page, "cancel_preview")).map(call => call.args)).toEqual([["memory-first"]]);
  expect(await calls(page, "confirm_memory")).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(page.locator("#hero")).toBeVisible();
});

test("Esc 不打斷執行中的套用，完成後返回原頁", async ({ page }) => {
  await boot(page);
  await page.locator("[data-cmd=apply]").click();
  await page.locator("#category-settings").check();
  await page.locator("#apply-preview").click();
  await page.evaluate(() => {
    window.pywebview!.api.confirm_apply = () => new Promise(resolve => {
      Object.assign(window, { finish: () => resolve({ code: 0, output: "完成", error: null, backup_path: null, recovery_required: false }) });
    });
  });
  await page.locator("#confirm-yes").click();
  await page.keyboard.press("Escape");
  await expect(page.locator("#output")).toBeVisible();
  await expect(page.locator("#output-back")).toBeDisabled();
  expect(await calls(page, "cancel_preview")).toHaveLength(0);
  await page.evaluate(() => (window as unknown as { finish: () => void }).finish());
  await expect(page.locator("#output-back")).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.locator("#apply-panel")).toBeVisible();
});

for (const width of [320, 375, 414, 768, 880]) {
  test(`記憶管理 ${width} 寬度無水平溢出，操作可達`, async ({ page }) => {
    await page.setViewportSize({ width, height: 680 });
    await boot(page);
    await expect(page.getByRole("button", { name: "記憶管理", exact: true })).toBeInViewport();
    if (width === 880) await page.screenshot({ path: test.info().outputPath("home.png") });
    await page.locator("#memory-open").click();
    await expect(page.locator("#memory-summary")).toContainText("已納入 Git");
    if (width === 880 || width === 320) await page.screenshot({ path: test.info().outputPath("memory.png") });
    expect(await page.locator("#memory-panel").evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    await page.locator("#memory-select-project").scrollIntoViewIfNeeded();
    await expect(page.locator("#memory-select-project")).toBeInViewport();
  });
}
