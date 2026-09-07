import { expect, test } from "@playwright/test";
import { boot, calls, queue, skills } from "./mock-bridge";

test("640 × 480 設定內容可捲動，最後一列仍可操作", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 480 });
  await boot(page);
  await page.locator("#settings-open").click();
  await expect(page.locator("#settings-account")).toHaveText("已授權");
  const dialog = page.getByRole("dialog", { name: "設定", exact: true });
  const rows = page.locator(".settings-rows");
  expect(await rows.evaluate((el) => el.clientHeight)).toBeGreaterThan(60);
  for (const selector of ["#settings-relogin", "#settings-open-dir"]) {
    const button = page.locator(selector);
    await button.scrollIntoViewIfNeeded();
    await expect(button).toBeInViewport();
    await button.click({ trial: true });
  }
  expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await page.locator("#settings-open-dir").click();
  await expect.poll(() => calls(page, "open_data_dir")).toHaveLength(1);
});

test("36 筆長技能名稱不造成水平裁切，清單末端與操作可達", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 480 });
  await boot(page);
  await page.locator("#package-open").click();
  const list = page.locator("#skill-list");
  await expect(list.getByRole("checkbox")).toHaveCount(36);
  const last = list.getByRole("checkbox").last();
  await last.check();
  await expect(last).toBeChecked();
  expect(await list.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  for (const id of ["#skill-share", "#skill-package", "#package-back"]) {
    await page.locator(id).scrollIntoViewIfNeeded();
    await expect(page.locator(id)).toBeInViewport();
    await page.locator(id).click({ trial: true });
  }
  await last.uncheck();
  await list.getByRole("checkbox").first().check();
  await page.locator("#skill-unshare").scrollIntoViewIfNeeded();
  await expect(page.locator("#skill-unshare")).toBeInViewport();
  await page.locator("#skill-unshare").click({ trial: true });
});

test("只檢查 Codex 時，摘要不宣稱所有工具或雲端一致", async ({ page }) => {
  await boot(page);
  await page.locator("[data-tool=codex]").click();
  await page.locator("[data-cmd=status]").click();
  await expect(page.locator("[data-tool-row=codex] .tool-state")).toHaveText("一致");
  await expect(page.locator("[data-tool-row=claude] .tool-state")).toContainText("未檢查");
  await expect(page.locator("#hero-title")).toContainText(/Codex/i);
  const summary = await page.locator("#hero-state").innerText();
  expect(summary).not.toMatch(/所有工具.*一致|設定已是最新/);
  expect(summary).not.toMatch(/(?:與|跟)雲端一致/);
  expect((await calls(page, "run"))[0].args).toEqual(["status", "codex"]);
});

test("有差異的狀態維持差異提示", async ({ page }) => {
  await boot(page, { run: [{
    code: 0, output: "═══ Status: codex ═══\n⚠ settings differ\n+ model = changed",
  }] });
  await page.locator("[data-tool=codex]").click();
  await page.locator("[data-cmd=status]").click();
  await expect(page.locator("[data-tool-row=codex] .tool-state")).toHaveText("有差異");
  await expect(page.locator("#hero-title")).toContainText("差異");
});

test("離開上傳預覽取消確認，再次預覽只能使用新 token", async ({ page }) => {
  await boot(page);
  await page.locator("[data-cmd=push]").click();
  await expect(page.locator("#output #confirm")).toBeVisible();
  await page.locator("#output-back").click();
  await expect(page.locator("#confirm")).toBeHidden();
  await expect(page.locator("[data-cmd=status]")).toBeEnabled();
  await page.locator("#output-toggle").click();
  await expect(page.locator("#confirm")).toBeHidden();
  expect(await calls(page, "confirm_push")).toHaveLength(0);
  await page.locator("#output-back").click();
  await queue(page, "preview_push", {
    code: 0, output: "+ new content", needs_confirmation: true, token: "preview-second",
  });
  await page.locator("[data-cmd=push]").click();
  await page.locator("#confirm-yes").click();
  await expect.poll(() => calls(page, "confirm_push")).toEqual([
    { method: "confirm_push", args: ["all", "preview-second"] },
  ]);
  await expect(page.locator("#confirm")).toBeHidden();
});

test("連線資訊直接呈現可見結果", async ({ page }) => {
  await boot(page);
  await page.locator("#config-info").click();
  await expect(page.locator("#output")).toBeVisible();
  await expect(page.locator("#output-body")).toContainText("provider: gdrive");
  await expect(page.locator("#output-body")).toBeInViewport();
});

test("打包成功後再次失敗會清除先前安裝說明", async ({ page }) => {
  await boot(page);
  await page.locator("#package-open").click();
  await page.locator("#skill-list").getByRole("checkbox").first().check();
  await page.locator("#skill-package").click();
  await expect(page.locator("#package-result")).toBeVisible();
  await expect(page.locator("#package-message")).toHaveValue(/skill\.zip/);
  await page.locator("#export-back").click();
  await queue(page, "package_skills", { code: 1, output: "✗ 無法寫入安裝檔", zips: [] });
  await page.locator("#skill-package").click();
  await expect(page.locator("#package-result")).toBeHidden();
  await expect(page.locator("#package-message")).toHaveValue("");
  await expect(page.locator("#skill-result")).toBeVisible();
  await expect(page.locator("#skill-result")).toContainText("無法寫入安裝檔");
});

test("更新檢查 Promise 拒絕後顯示錯誤並允許重試", async ({ page }) => {
  await boot(page, { check_update: [{ reject: "測試網路中斷" }] });
  await page.locator("#update-check").click();
  await expect(page.locator("#update-check")).toBeEnabled();
  await expect(page.locator("#output")).toBeVisible();
  await expect(page.locator("#output-body")).toContainText("測試網路中斷");
  await page.locator("#update-check").click();
  await expect.poll(() => calls(page, "check_update")).toHaveLength(2);
  await expect(page.locator("#update-check")).toBeEnabled();
  await page.locator("#output-back").click();
  await expect(page.locator("[data-cmd=status]")).toBeEnabled();
});

test("設定對話框正向與反向 Tab 都不會移到背景", async ({ page }) => {
  await boot(page);
  await page.locator("#settings-open").click();
  await expect(page.locator("#settings-account")).toHaveText("已授權");
  for (const key of ["Tab", "Shift+Tab"]) {
    for (let step = 0; step < 12; step++) {
      await page.keyboard.press(key);
      expect(await page.evaluate(() =>
        document.querySelector("#settings")!.contains(document.activeElement),
      )).toBe(true);
    }
  }
  await page.keyboard.press("Escape");
  await expect(page.locator("#settings")).toBeHidden();
  await expect(page.locator("#settings-open")).toBeFocused();
});

test("重新登入使用獨立 API，結果留在設定內可見", async ({ page }) => {
  await boot(page);
  await page.locator("#settings-open").click();
  await page.locator("#settings-relogin").click();
  await expect.poll(() => calls(page, "relogin_gdrive")).toHaveLength(1);
  expect(await calls(page, "setup_gdrive")).toHaveLength(0);
  expect(await calls(page, "setup_repo")).toHaveLength(0);
  await expect(page.locator("#settings-feedback")).toBeVisible();
  await expect(page.locator("#settings-feedback")).toContainText("重新登入");
});

test("技能搜尋與篩選保留選取，計數隨操作更新", async ({ page }) => {
  await boot(page);
  await page.locator("#package-open").click();
  await expect(page.locator("#skill-selection")).toContainText(/已選(?:取)?\s*0(?:\s|個|項|$)/);
  await page.locator("#skill-search").fill("skill-01-");
  await expect(page.locator("#skill-list").getByRole("checkbox")).toHaveCount(1);
  await page.locator("#skill-list").getByRole("checkbox").check();
  await expect(page.locator("#skill-selection")).toContainText(/已選(?:取)?\s*1(?:\s|個|項|$)/);
  await page.locator("#skill-search").fill("");
  await page.locator("#skill-filter").selectOption("shared");
  await expect(page.locator("#skill-list").getByRole("checkbox")).toHaveCount(18);
  await expect(page.locator("#skill-list").getByRole("checkbox").first()).toBeChecked();
  await page.locator("#skill-package").click();
  expect((await calls(page, "package_skills"))[0].args).toEqual([[skills[0].name]]);
  await page.locator("#export-back").click();
  await page.locator("#skill-none").click();
  await expect(page.locator("#skill-selection")).toContainText(/已選(?:取)?\s*0(?:\s|個|項|$)/);
});

test("技能載入失敗可明確重試", async ({ page }) => {
  await boot(page, { list_skills: [{ reject: "測試技能讀取失敗" }] });
  await page.locator("#package-open").click();
  await expect(page.locator("#skill-retry")).toBeVisible();
  await page.locator("#skill-retry").click();
  await expect(page.locator("#skill-list").getByRole("checkbox")).toHaveCount(36);
  await expect(page.locator("#skill-retry")).toBeHidden();
});
