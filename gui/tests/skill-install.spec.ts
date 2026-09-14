import { expect, test } from "@playwright/test";
import { boot, calls, queue, skills } from "./mock-bridge";

test("選擇資料夾後才可安裝，成功重新載入清單並清除來源", async ({ page }) => {
  await boot(page);
  await page.locator("#package-open").click();
  await expect(page.locator("#skill-add")).toBeDisabled();
  await page.locator("#skill-pick").click();
  await expect(page.locator("#skill-source")).toHaveText("/tmp/local skills/example");
  expect(await calls(page, "add_skill")).toHaveLength(0);
  await expect(page.locator("#skill-add")).toBeEnabled();
  await queue(page, "add_skill", { code: 0, output: "✓ Installed example" });
  await queue(page, "list_skills", {
    skills: [...skills, { name: "example", shared: false, shareable: true }],
  });
  const before = (await calls(page, "list_skills")).length;
  await page.locator("#skill-add").click();
  await expect(page.locator("#skill-result")).toContainText("安裝完成");
  expect((await calls(page, "add_skill"))[0].args).toEqual([
    "/tmp/local skills/example",
  ]);
  expect(await calls(page, "list_skills")).toHaveLength(before + 1);
  await expect(page.locator("#skill-list").getByRole("checkbox", { name: "example", exact: true })).toBeVisible();
  await expect(page.locator("#skill-source")).toHaveText("尚未選擇資料夾");
  await expect(page.locator("#skill-add")).toBeDisabled();
  await page.locator("#skill-output").click();
  await expect(page.locator("#output-body")).toContainText("Installed example");
});

test("取消重新選擇會清除舊來源，不會安裝", async ({ page }) => {
  await boot(page);
  await page.locator("#package-open").click();
  await page.locator("#skill-pick").click();
  await expect(page.locator("#skill-add")).toBeEnabled();
  await queue(page, "select_skill_directory", {
    code: 0, output: "", cancelled: true, path: null,
  });
  await page.locator("#skill-pick").click();
  await expect(page.locator("#skill-add")).toBeDisabled();
  await expect(page.locator("#skill-source")).toHaveText("尚未選擇資料夾");
  expect(await calls(page, "add_skill")).toHaveLength(0);
});

for (const failure of [
  { code: 1, output: "無法開啟原生視窗", cancelled: false, path: null },
  { reject: "選擇器連線中斷" },
]) {
  test(`目錄選擇失敗可重試：${JSON.stringify(failure)}`, async ({ page }) => {
    await boot(page, { select_skill_directory: [failure] });
    await page.locator("#package-open").click();
    await page.locator("#skill-pick").click();
    await expect(page.locator("#skill-result")).toHaveClass(/is-fail/);
    await expect(page.locator("#skill-pick")).toBeEnabled();
    await expect(page.locator("#skill-add")).toBeDisabled();
    expect(await calls(page, "add_skill")).toHaveLength(0);
    await page.locator("#skill-pick").click();
    await expect(page.locator("#skill-add")).toBeEnabled();
  });
}

for (const failure of [
  { code: 1, output: "✗ Skill already exists; refusing to overwrite" },
  { reject: "安裝連線中斷" },
]) {
  test(`安裝失敗保留來源、顯示錯誤並可重試：${JSON.stringify(failure)}`, async ({ page }) => {
    await boot(page, { add_skill: [failure] });
    await page.locator("#package-open").click();
    await page.locator("#skill-pick").click();
    const before = (await calls(page, "list_skills")).length;
    await page.locator("#skill-add").click();
    await expect(page.locator("#skill-result")).toHaveClass(/is-fail/);
    await expect(page.locator("#skill-result")).not.toContainText("安裝完成");
    await expect(page.locator("#skill-add")).toBeEnabled();
    await expect(page.locator("#skill-source")).toHaveText("/tmp/local skills/example");
    expect(await calls(page, "list_skills")).toHaveLength(before);
  });
}

test("安裝中禁止重複操作，完成後恢復", async ({ page }) => {
  await boot(page);
  await page.locator("#package-open").click();
  await page.locator("#skill-pick").click();
  await page.evaluate(() => {
    window.pywebview!.api.add_skill = () => new Promise(resolve => {
      Object.assign(window, { finishInstall: () => resolve({ code: 0, output: "✓ 完成" }) });
    });
  });
  await page.locator("#skill-add").click();
  for (const id of ["#skill-add", "#skill-pick", "#skill-share", "#package-back"]) {
    await expect(page.locator(id)).toBeDisabled();
  }
  await page.evaluate(() => (window as unknown as { finishInstall: () => void }).finishInstall());
  await expect(page.locator("#skill-pick")).toBeEnabled();
  await expect(page.locator("#skill-add")).toBeDisabled();
});

for (const width of [320, 640]) {
  test(`安裝來源長路徑在 ${width} 寬度不溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 480 });
    const path = "/tmp/" + "很長的技能資料夾".repeat(25);
    await boot(page, { select_skill_directory: [{ code: 0, output: "", cancelled: false, path }] });
    await page.locator("#package-open").click();
    await page.locator("#skill-pick").click();
    await expect(page.locator("#skill-source")).toHaveText(path);
    expect(await page.locator("#package").evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    await page.locator("#skill-add").scrollIntoViewIfNeeded();
    await expect(page.locator("#skill-add")).toBeInViewport();
    await page.locator("#skill-add").click({ trial: true });
    await expect(page.getByRole("button", { name: /刪除技能|移除技能/ })).toHaveCount(0);
  });
}
