import { expect, test, type Page } from "@playwright/test";
import { boot } from "./mock-bridge";

async function openWith(page: Page, handoffs: object[] | null) {
  await boot(page);
  await page.evaluate((handoffs) => {
    const bridge = window.pywebview!.api;
    const original = bridge.memory_info;
    bridge.memory_info = async () => {
      const info = await original();
      if (handoffs === null) delete (info as { handoffs?: unknown }).handoffs;
      else (info as { handoffs?: unknown }).handoffs = handoffs;
      return info;
    };
  }, handoffs);
  await page.locator("#memory-open").click();
}

test("列出待接手的工作線與過期標示", async ({ page }) => {
  await openWith(page, [
    { thread: "排程", project: "CSL426--ai-config", state: "open",
      age_days: 0, stale: false, summary: "還剩:等 Windows 回報" },
    { thread: "admin 拆分", project: "CreateIntelligens--openVman", state: "open",
      age_days: 7, stale: true, summary: "還剩:push 並開 PR" },
  ]);
  const rows = page.locator(".handoff-thread");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1)).toHaveAttribute("data-stale", "true");
  await expect(rows.nth(1)).toContainText("7 天前開的 · ⚠ 可能已過期");
  // 接走就結案,列表上不會有「誰持有」這回事
  await expect(rows.nth(0)).not.toContainText("持有");
  await expect(page.locator("#handoff-threads-empty")).toBeEmpty();
});

test("舊版後端沒有 handoffs 也能開啟，顯示沒有工作線", async ({ page }) => {
  await openWith(page, null);
  await expect(page.locator(".handoff-thread")).toHaveCount(0);
  await expect(page.locator("#handoff-threads-empty")).toHaveText("目前沒有待接手的工作線。");
});
