import { expect, test } from "@playwright/test";
import { boot, calls, queue } from "./mock-bridge";

const success = { code: 0, output: "完成", error: null, backup_path: null, recovery_required: false };
const projectInfo = {
  ...success, data_root: "/tmp/data/memory", shared_path: "/tmp/shared-memory",
  shared_status: "ok", tracked: true, git_status: "dirty", changed_paths: [], entries: [],
  project: { root: "/tmp/project", key: "owner--project", stable: true,
    memory_path: "/tmp/memory/projects/owner--project", journal_path: "/tmp/project/.remember",
    journal_status: "adopted", remember_installed: false },
  actions: {
    enable: { allowed: true, reason: "" }, disable: { allowed: true, reason: "" },
    adopt: { allowed: false, reason: "remember 未安裝" },
    release: { allowed: true, reason: "" }, push: { allowed: true, reason: "" },
  },
  locations: [{ label: "專案日誌", path: "/tmp/project/.remember", token: "location-project" }],
};

async function applyPreview(page: Parameters<typeof boot>[0]) {
  await page.locator("[data-cmd=apply]").click();
  await page.locator("#category-settings").check();
  await page.locator("#apply-preview").click();
  await expect(page.locator("#confirm")).toBeVisible();
}

test("套用預設空選、交集傳遞與取消後關閉重設", async ({ page }) => {
  await boot(page);
  await page.locator("[data-tool=codex]").click();
  await page.locator("[data-cmd=apply]").click();
  await expect(page.locator("#apply-preview")).toBeDisabled();
  await expect(page.locator("#category-settings")).not.toBeChecked();
  await page.locator("#category-skills").check();
  await page.locator("#apply-preview").click();
  expect((await calls(page, "preview_apply"))[0].args).toEqual(["codex", "skills"]);
  await expect(page.locator("#confirm-yes")).toContainText("獨立技能");
  await expect(page.locator("#preview-changes")).toContainText("/tmp/tools/CLAUDE.md");
  await expect(page.locator("#confirm-text")).toBeFocused();
  await page.locator("#confirm-no").click();
  expect((await calls(page, "cancel_preview"))[0].args).toEqual(["apply-first"]);
  await expect(page.locator("#apply-preview")).toBeFocused();
  await page.locator("#apply-back").click();
  await page.locator("[data-cmd=apply]").click();
  await expect(page.locator("#category-skills")).not.toBeChecked();
  await expect(page.locator("#apply-preview")).toBeDisabled();
  expect(await calls(page, "confirm_apply")).toHaveLength(0);
  expect((await calls(page, "run")).filter(call => call.args[0] === "apply")).toHaveLength(0);
});

test("空變更顯示已一致且不確認，文字差異不當 HTML 執行", async ({ page }) => {
  await boot(page, { preview_apply: [{ ...success, output: "<img src=x onerror=alert(1)>",
    token: "", needs_confirmation: false, scope: { tool: "all", category: "all" },
    changes: [], warnings: [] }] });
  await page.locator("[data-cmd=apply]").click();
  await page.locator("#category-settings").check();
  await page.locator("#category-skills").check();
  await page.locator("#apply-preview").click();
  expect((await calls(page, "preview_apply"))[0].args).toEqual(["all", "all"]);
  await expect(page.locator("#output-state")).toHaveText("已一致");
  await expect(page.locator("#confirm")).toBeHidden();
  await expect(page.locator("#output-body img")).toHaveCount(0);
  await expect(page.locator("#output-body")).toContainText("<img");
});

test("BUSY 保留 token，STALE_PREVIEW 消耗並顯示錯誤", async ({ page }) => {
  await boot(page, { confirm_apply: [
    { ...success, code: 1, error: "BUSY", output: "另一操作執行中" },
    { ...success, code: 1, error: "STALE_PREVIEW", output: "來源已變更" },
  ] });
  await applyPreview(page);
  await page.locator("#confirm-yes").click();
  await expect(page.locator("#confirm")).toBeVisible();
  await expect(page.locator("#confirm-text")).toContainText("仍有效");
  await page.locator("#confirm-yes").click();
  await expect(page.locator("#confirm")).toBeHidden();
  await expect(page.locator("#output-body")).toContainText("STALE_PREVIEW");
  expect((await calls(page, "confirm_apply")).map(call => call.args)).toEqual([["apply-first"], ["apply-first"]]);
  expect(await calls(page, "cancel_preview")).toHaveLength(0);
});

test("取消失敗保留預覽，執行中停用範圍與取消", async ({ page }) => {
  await boot(page, { cancel_preview: [{ ...success, code: 1, error: "BUSY", output: "請稍後取消" }] });
  await applyPreview(page);
  await page.locator("#confirm-no").click();
  await expect(page.locator("#confirm")).toBeVisible();
  await expect(page.locator("#output-body")).toContainText("BUSY");
  await page.evaluate(() => {
    window.pywebview!.api.confirm_apply = () => new Promise(resolve => {
      Object.assign(window, { finishApply: () => resolve({ code: 0, output: "套用完成", error: null, backup_path: null, recovery_required: false }) });
    });
  });
  await page.locator("#confirm-yes").click();
  await expect(page.locator("#output-back")).toBeDisabled();
  await expect(page.locator("#category-settings")).toBeDisabled();
  await page.evaluate(() => (window as unknown as { finishApply: () => void }).finishApply());
  await expect(page.locator("#output-back")).toBeEnabled();
});

test("下載永遠 all、不自動套用，提供後續預覽", async ({ page }) => {
  await boot(page);
  await page.locator("[data-tool=codex]").click();
  await expect(page.locator(".command-hint")).toContainText("共用記憶會立即更新");
  await page.locator("[data-cmd=pull]").click();
  await expect(page.locator("#pull-apply")).toBeVisible();
  expect((await calls(page, "run"))[0].args).toEqual(["pull", "all"]);
  expect(await calls(page, "preview_apply")).toHaveLength(0);
  await page.locator("#pull-apply").click();
  await expect(page.locator("#apply-preview")).toBeDisabled();
});

test("記憶入口區分安裝與 runtime、未選專案停用、開啟使用位置 token", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 480 });
  await boot(page);
  await page.locator("#memory-open").click();
  await expect(page.locator("#memory-entries")).toContainText("規則已安裝，請開新會話驗證");
  await expect(page.locator("#memory-entries")).toContainText("override 遮蔽入口");
  await expect(page.locator("[data-memory-action=adopt]")).toBeDisabled();
  await expect(page.locator("[data-memory-action=release]")).toBeDisabled();
  await page.locator("#memory-locations button").click();
  expect((await calls(page, "open_memory_location"))[0].args).toEqual(["location-global"]);
  expect(await page.locator("#memory-panel").evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await page.locator("[data-memory-action=enable]").click();
  expect((await calls(page, "preview_memory"))[0].args).toEqual(["enable"]);
  await page.locator("#confirm-no").click();
  expect((await calls(page, "cancel_preview"))[0].args).toEqual(["memory-first"]);
});

test("原生選專案後取消保留專案，無 remember 仍可 release", async ({ page }) => {
  await boot(page);
  await page.locator("#memory-open").click();
  await queue(page, "select_memory_project", { ...success, cancelled: false,
    project_token: "project-issued", root: "/tmp/project", key: "owner--project", stable: true });
  await queue(page, "memory_info", projectInfo);
  await page.locator("#memory-select-project").click();
  await expect(page.locator("#memory-project")).toContainText("owner--project");
  expect((await calls(page, "memory_info")).at(-1)?.args).toEqual(["project-issued"]);
  await page.locator("#memory-select-project").click();
  await expect(page.locator("#memory-project")).toContainText("owner--project");
  await expect(page.locator("[data-memory-action=adopt]")).toBeDisabled();
  await expect(page.locator("[data-memory-action=release]")).toBeEnabled();
  await page.locator("[data-memory-action=release]").click();
  expect((await calls(page, "preview_memory"))[0].args).toEqual(["release", "project-issued"]);
  await expect(page.locator("#confirm-text")).toContainText("其他機器");
  await page.locator("#confirm-yes").click();
  expect((await calls(page, "confirm_memory"))[0].args).toEqual(["memory-first"]);
  expect(await calls(page, "confirm_push")).toHaveLength(0);
});

test("上傳記憶顯示完整 commit 範圍且 confirmation 綁 memory", async ({ page }) => {
  await boot(page, { preview_push: [{ ...success, token: "memory-push", needs_confirmation: true,
    scope: "memory", changed_paths: ["memory/MEMORY.md"], outgoing_commits: ["abc123 memory update", "def456 prior commit"] }] });
  await page.locator("#memory-open").click();
  await page.locator("#memory-push").click();
  expect((await calls(page, "preview_push"))[0].args).toEqual(["memory"]);
  await expect(page.locator("#output-body")).toContainText("def456 prior commit");
  await expect(page.locator("#confirm-text")).not.toContainText("已收集本機設定");
  await page.locator("#confirm-yes").click();
  expect((await calls(page, "confirm_push"))[0].args).toEqual(["memory", "memory-push"]);
});
