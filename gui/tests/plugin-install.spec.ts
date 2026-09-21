import { expect, test } from "@playwright/test";
import { boot } from "./mock-bridge";

test("設定裡有外掛安裝指令,可複製", async ({ page }) => {
  await boot(page);
  await page.locator("#settings-open").click();
  await expect(page.locator("#settings-account")).toHaveText("已授權");

  const box = page.locator("#plugin-install");
  await expect(box).toHaveValue(/claude plugin marketplace add CSL426\/ai-config/);
  await expect(box).toHaveValue(/claude plugin install acg@acg/);

  await page.locator("#plugin-copy").click();
  await expect(page.locator("#plugin-copy")).toHaveText(/已複製|請按複製快捷鍵/);
});

test("斜線指令都列在說明裡", async ({ page }) => {
  await boot(page);
  await page.locator("#settings-open").click();
  await expect(page.locator("#settings-account")).toHaveText("已授權");

  const hint = page.locator(".settings-group", { has: page.locator("#plugin-install") })
    .locator(".package-hint");
  for (const name of ["status", "sync", "save", "share", "memory", "handoff", "handoffs", "pickup", "keepalive"]) {
    await expect(hint).toContainText(`/acg:${name}`);
  }
});

test("安裝指令在窄視窗不溢出", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 720 });
  await boot(page);
  await page.locator("#settings-open").click();
  await expect(page.locator("#settings-account")).toHaveText("已授權");

  // textarea 自己可以橫向捲動,那是正常的;要守的是它不撐破容器、
  // 也不讓整頁出現橫向捲軸
  const box = page.locator("#plugin-install");
  await expect(box).toBeVisible();
  const fits = await box.evaluate((el) => {
    const group = el.closest(".settings-group") as HTMLElement;
    return {
      withinGroup: el.getBoundingClientRect().width <= group.clientWidth + 1,
      pageOverflow: document.documentElement.scrollWidth
        - document.documentElement.clientWidth,
    };
  });
  expect(fits.withinGroup).toBe(true);
  expect(fits.pageOverflow).toBeLessThanOrEqual(1);
});
