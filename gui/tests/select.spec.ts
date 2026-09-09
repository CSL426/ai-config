import { expect, test, type Page } from "@playwright/test";
import { boot } from "./mock-bridge";

async function openSkills(page: Page) {
  await boot(page);
  await page.locator("#package-open").click();
  const trigger = page.getByRole("combobox", { name: "顯示", exact: true });
  await expect(trigger).toBeEnabled();
  return trigger;
}

test("自訂下拉保留技能篩選與原生 change 同步", async ({ page }) => {
  const trigger = await openSkills(page);
  await expect(page.locator("#skill-filter")).toBeHidden();
  await trigger.click();
  await expect(page.getByRole("listbox", { name: "顯示" })).toBeVisible();
  await page.getByRole("option", { name: "已分享", exact: true }).click();
  await expect(page.locator("#skill-filter")).toHaveValue("shared");
  await expect(trigger).toContainText("已分享");
  await expect(page.locator("#skill-list").getByRole("checkbox")).toHaveCount(18);
  await page.locator("#skill-filter").selectOption("unshared", { force: true });
  await expect(trigger).toContainText("未分享");
  await page.locator("#skill-filter").evaluate((element: HTMLSelectElement) => { element.value = "all"; });
  await expect(trigger).toContainText("全部技能");
});

test("Escape 只關閉選單，外部點擊與 Tab 不困住焦點", async ({ page }) => {
  const trigger = await openSkills(page);
  await trigger.click();
  await page.keyboard.press("Escape");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(trigger).toBeFocused();
  await expect(page.locator("#package")).toBeVisible();
  await trigger.click();
  await page.locator("#skill-search").click();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.focus();
  await page.keyboard.press("Space");
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Tab");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(trigger).not.toBeFocused();
});

test("方向鍵跳過停用選項，Home End 與 typeahead 可選取動態項目", async ({ page }) => {
  const trigger = await openSkills(page);
  await page.locator("#skill-filter").evaluate((element: HTMLSelectElement) => {
    element.replaceChildren(new Option("Alpha", "a"), new Option("Beta", "b"), new Option("Bravo", "br"), new Option("Delta", "d"));
    element.options[1].disabled = true;
    element.value = "a";
  });
  await trigger.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator(".acg-select-option.is-active")).toHaveText("Bravo");
  await page.keyboard.press("End");
  await expect(page.locator(".acg-select-option.is-active")).toHaveText("Delta");
  await page.keyboard.press("Home");
  await expect(page.locator(".acg-select-option.is-active")).toHaveText("Alpha");
  await page.keyboard.press("b");
  await expect(page.locator(".acg-select-option.is-active")).toHaveText("Bravo");
  await page.keyboard.press("Enter");
  await expect(page.locator("#skill-filter")).toHaveValue("br");
  await trigger.click();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Space");
  await expect(page.locator("#skill-filter")).toHaveValue("a");
});

test("動態 select、disabled 與 label 關聯同步", async ({ page }) => {
  await openSkills(page);
  await page.evaluate(() => {
    const fieldset = document.createElement("fieldset");
    fieldset.id = "select-fixture";
    fieldset.innerHTML = '<label for="fixture-select">測試範圍</label><select id="fixture-select"><option value="a">甲</option><option value="b">乙</option></select>';
    document.querySelector("#package")!.prepend(fieldset);
  });
  const trigger = page.getByRole("combobox", { name: "測試範圍" });
  await expect(trigger).toBeVisible();
  await page.locator('label[for="fixture-select"]').click();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.locator("#select-fixture").evaluate((element: HTMLFieldSetElement) => { element.disabled = true; });
  await expect(trigger).toBeDisabled();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await page.locator("#select-fixture").evaluate((element: HTMLFieldSetElement) => { element.disabled = false; });
  await expect(trigger).toBeEnabled();
  await page.locator("#fixture-select").evaluate((element: HTMLSelectElement) => {
    element.disabled = true;
    element.setAttribute("aria-label", "新的範圍");
  });
  await expect(page.getByRole("combobox", { name: "新的範圍" })).toBeDisabled();
});

test("窄視窗的選單脫離裁切容器且維持在畫面內", async ({ page }) => {
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({ width, height: 640 });
    if (width === 320) await openSkills(page);
    await page.locator("#skill-filter").evaluate((element: HTMLSelectElement) => {
      element.replaceChildren(...Array.from({ length: 30 }, (_, index) => new Option(`選項 ${index}`, String(index))));
    });
    const trigger = page.getByRole("combobox", { name: "顯示", exact: true });
    await trigger.click();
    const menu = page.getByRole("listbox", { name: "顯示", exact: true });
    await expect(menu).toBeVisible();
    if (width === 768) await page.screenshot({ path: test.info().outputPath("dropdown.png") });
    expect(await menu.evaluate(element => element.parentElement === document.body)).toBe(true);
    const box = await menu.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(641);
    await page.keyboard.press("Escape");
  }
});


test("輸入法組字按鍵不觸發選擇或關閉", async ({ page }) => {
  const trigger = await openSkills(page);
  await trigger.click();
  const initial = await trigger.getAttribute("aria-activedescendant");
  for (const key of ["ArrowDown", "Enter", "Escape"]) {
    await trigger.dispatchEvent("keydown", { key, isComposing: true, bubbles: true });
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await expect(trigger).toHaveAttribute("aria-activedescendant", initial!);
  }
  await expect(page.locator("#package")).toBeVisible();
});
