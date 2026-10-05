import { expect, test } from "@playwright/test";
import { boot, calls } from "./mock-bridge";

// 狀態每次更新都會重查一次,同一個答案要給足夠次數
const always = (info: object) => Array.from({ length: 8 }, () => info);

const DONE = {
  configured: true, repo_empty: false, applied: true,
  memory: true, autopush: true, autoupdate: true,
};

test("a machine that is fully set up shows no next-step card", async ({ page }) => {
  await boot(page);
  await expect(page.locator("#next-step")).toBeHidden();
});

test("the first machine is told to upload, and the button previews a push of everything", async ({ page }) => {
  await boot(page, { onboarding_info: always({ ...DONE, repo_empty: true, applied: false, memory: false }) });
  const card = page.locator("#next-step");
  await expect(card).toBeVisible();
  await expect(card.getByRole("heading")).toHaveText("這台是第一台");
  await expect(page.locator("#next-step-suggest")).toBeVisible();
  // 還有一步沒做時不給「不再提示」,做完才給
  await expect(page.locator("#next-step-dismiss")).toBeHidden();
  await page.locator("#next-step-go").click();
  await expect.poll(async () => (await calls(page, "preview_push")).map((c) => c.args[0])).toEqual(["all"]);
});

test("a machine that never applied is told to apply first", async ({ page }) => {
  await boot(page, { onboarding_info: always({ ...DONE, applied: false }) });
  await expect(page.locator("#next-step").getByRole("heading")).toHaveText("這台還沒套用置物櫃的設定");
  await expect(page.locator("#next-step-go")).toHaveText("套用設定");
  // 功能都開好了,就只剩那一步
  await expect(page.locator("#next-step-suggest")).toBeHidden();
  await page.locator("#next-step-go").click();
  await expect(page.locator("#apply-panel")).toBeVisible();
});

test("suggestions mark what is on, open the right page, and can be dismissed for good", async ({ page }) => {
  await boot(page, { onboarding_info: always({ ...DONE, autopush: false, autoupdate: false }) });
  const card = page.locator("#next-step");
  await expect(card).toBeVisible();
  await expect(page.locator("#next-step-main")).toBeHidden();
  await expect(card.locator('[data-feature="memory"]')).toHaveClass(/is-done/);
  await expect(card.locator('[data-feature="autopush"]')).not.toHaveClass(/is-done/);

  await card.getByRole("button", { name: "每晚保存筆記" }).click();
  await expect(page.locator("#automation-panel")).toBeVisible();
  await page.locator("#automation-back").click();

  await page.locator("#next-step-dismiss").click();
  await expect(card).toBeHidden();
  await page.reload();
  await expect(page.locator("[data-cmd=status]")).toBeEnabled();
  await expect(card).toBeHidden();
});
