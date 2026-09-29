import { expect, test } from "@playwright/test";
import { boot, calls, queue } from "./mock-bridge";

const success = { code: 0, output: "完成", error: null, backup_path: null, recovery_required: false };
const chosen = { ...success, cancelled: false, project_token: "project-1", root: "/tmp/project" };
const items = [
  { name: "CLAUDE.md", note: "Claude", kind: "claude" },
  { name: "skills/wiki888", note: "Claude, Codex, agy", kind: "skill" },
  { name: "plugins/frontend-design@claude-plugins-official", note: "Claude plugin,全域沒開", kind: "plugin" },
  { name: "memory", note: "Claude, Codex, agy", kind: "memory" },
];
const info = (deployed: object | null) => ({ ...success, root: "/tmp/project", items, deployed });
const nothingPlaced = { files: 0, plugins: [], memory: false, paths: [] };

async function openWithProject(page: Parameters<typeof boot>[0], deployed: object = nothingPlaced) {
  await boot(page);
  await page.locator("#deploy-open").click();
  await expect(page.locator("#deploy-title")).toBeFocused();
  await queue(page, "select_deploy_project", chosen);
  await queue(page, "deploy_info", info(deployed));
  await page.locator("#deploy-select").click();
  await expect(page.locator("#deploy-root")).toHaveText("/tmp/project");
}

test("選專案前不能勾選或預覽,分組列出技能、plugin 與記憶規則", async ({ page }) => {
  await boot(page);
  await page.locator("#deploy-open").click();
  await expect(page.locator("#deploy-preview")).toBeDisabled();
  await expect(page.locator("#deploy-remove")).toBeDisabled();
  await expect(page.locator("#deploy-items input").first()).toBeDisabled();
  await expect(page.locator(".deploy-group legend")).toHaveText([
    "技能（2）", "Claude plugin（1）", "共用記憶（1）", "Claude 規則與指令（1）",
  ]);
});

test("勾選後預覽,確認列說明不動全域,確認只呼叫部署", async ({ page }) => {
  await openWithProject(page);
  await page.getByRole("checkbox", { name: /wiki888/ }).check();
  await page.getByRole("checkbox", { name: /記憶規則/ }).check();
  await expect(page.locator("#deploy-selection")).toHaveText("已選 2 項");
  await page.locator("#deploy-preview").click();

  expect((await calls(page, "preview_deploy"))[0].args).toEqual(["project-1", ["skills/wiki888", "memory"]]);
  await expect(page.locator("#preview-changes")).toContainText("/tmp/project/.agents/skills/wiki888");
  await expect(page.locator("#preview-changes")).toContainText("不覆蓋");
  await expect(page.locator("#confirm-text")).toContainText("不動這台電腦的全域設定");
  await expect(page.locator("#confirm-yes")).toContainText("1 項變更");

  await queue(page, "deploy_info", info({ files: 2, plugins: [], memory: true, paths: [] }));
  await page.locator("#confirm-yes").click();
  expect((await calls(page, "confirm_deploy"))[0].args).toEqual(["deploy-first"]);
  expect(await calls(page, "confirm_memory")).toHaveLength(0);
  expect((await calls(page, "run")).filter(call => call.args[0] === "apply")).toHaveLength(0);
});

test("取消預覽不部署,回到面板", async ({ page }) => {
  await openWithProject(page);
  await page.getByRole("checkbox", { name: /wiki888/ }).check();
  await page.locator("#deploy-preview").click();
  await page.locator("#confirm-no").click();
  expect((await calls(page, "cancel_preview"))[0].args).toEqual(["deploy-first"]);
  expect(await calls(page, "confirm_deploy")).toHaveLength(0);
  await expect(page.locator("#deploy-panel")).toBeVisible();
});

test("有部署紀錄才能收回,並列出已放入的內容", async ({ page }) => {
  await openWithProject(page, { files: 3, plugins: ["frontend-design@claude-plugins-official"], memory: true, paths: [] });
  await expect(page.locator("#deploy-placed")).toContainText("3 個檔案");
  await expect(page.locator("#deploy-placed")).toContainText("frontend-design");
  await expect(page.locator("#deploy-placed")).toContainText("記憶規則");
  await page.locator("#deploy-remove").click();
  expect((await calls(page, "preview_undeploy"))[0].args).toEqual(["project-1"]);
  await expect(page.locator("#confirm-text")).toContainText("改過的會保留");
  await page.locator("#confirm-yes").click();
  expect((await calls(page, "confirm_undeploy"))[0].args).toEqual(["undeploy-first"]);
});

test("沒有要變更時不要求確認", async ({ page }) => {
  await openWithProject(page);
  await queue(page, "preview_deploy", { ...success, token: "", needs_confirmation: false,
    scope: {}, warnings: [], changes: [] });
  await page.getByRole("checkbox", { name: /wiki888/ }).check();
  await page.locator("#deploy-preview").click();
  await expect(page.locator("#output-state")).toHaveText("都已在專案裡，不需要變更");
  await expect(page.locator("#confirm")).toBeHidden();
});

test("專案資料夾失效時清掉選擇並提示", async ({ page }) => {
  await openWithProject(page);
  await queue(page, "deploy_info", { ...success, code: 1, output: "專案資料夾或資料庫已變動,請重新選擇",
    error: "STALE_PREVIEW", items, root: null, deployed: null });
  await page.locator("#deploy-back").click();
  await page.locator("#deploy-open").click();
  await expect(page.locator("#deploy-feedback")).toContainText("請重新選擇");
  await expect(page.locator("#deploy-root")).toHaveText("尚未選擇專案。");
  await expect(page.locator("#deploy-preview")).toBeDisabled();
});

for (const width of [320, 640]) {
  test(`長路徑與長名稱在 ${width} 寬度不溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 700 });
    await boot(page);
    await page.locator("#deploy-open").click();
    const long = "/tmp/" + "very-long-project-folder-name/".repeat(6) + "app";
    await queue(page, "select_deploy_project", { ...chosen, root: long });
    await queue(page, "deploy_info", { ...success, root: long, deployed: nothingPlaced, items: [
      ...items, { name: "skills/" + "long-skill-name-".repeat(6) + "end", note: "Claude, Codex, agy", kind: "skill" },
    ] });
    await page.locator("#deploy-select").click();
    await expect(page.locator("#deploy-root")).toHaveText(long);
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
