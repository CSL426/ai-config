import { test } from "@playwright/test";
import { boot, queue } from "../tests/mock-bridge";

// 圖會放進公開的 README:只用中性的名稱與路徑,不放任何真實機器或專案
const out = (name: string) => `../docs/screenshots/${name}.png`;
const ok = { code: 0, output: "", error: null, backup_path: null, recovery_required: false };

const skills = ["code-review", "commit-helper", "pdf-report", "slides", "sql-explain", "test-writer"]
  .map((name, index) => ({ name, shared: index % 2 === 0, shareable: true }));

const memory = {
  ...ok, data_root: "~/ai-config/data/memory", shared_path: "~/.claude/shared-memory",
  shared_status: "ok", tracked: true, git_status: "clean", changed_paths: [],
  index_unlisted: [], index_dangling: [], secret_notes: [],
  entries: [
    { tool: "claude", status: "installed", reason: "", path: "~/.claude/CLAUDE.md", cli_installed: true },
    { tool: "codex", status: "installed", reason: "", path: "~/.codex/AGENTS.md", cli_installed: true },
    { tool: "agy", status: "installed", reason: "", path: "~/.gemini/GEMINI.md", cli_installed: true },
  ],
  project: null, locations: [{ label: "全域記憶", path: "~/ai-config/data/memory", token: "location-global" }],
  actions: {
    enable: { allowed: false, reason: "已一致" }, disable: { allowed: true, reason: "" },
    adopt: { allowed: false, reason: "請先選擇本機專案" }, release: { allowed: false, reason: "請先選擇本機專案" },
    push: { allowed: true, reason: "" },
  },
  handoffs: [{ thread: "release checklist", project: "owner--repo", state: "open",
    age_days: 0, stale: false, summary: "發布前跑三平台測試、打 tag" }],
  handoff_reminder: { enabled: true, threshold: 70, installed: true },
  remember_hosts: {
    codex: { available: true, installed: true, version: "0.3.0", trusted: true, detail: "" },
    agy: { available: true, installed: true, version: "0.3.0", trusted: null, detail: "" },
  },
  autopush: { installed: true, scheduled: true, last_push: "2026-09-30T20:10:00+00:00", reason: "",
    last_failure: null, slot: "04:10", host: "workstation",
    others: [{ host: "laptop", slot: "04:20" }, { host: "desktop", slot: "04:00" }] },
  autoupdate: { installed: true, time: "04:10", last_run: "2026-09-30T20:10:00+00:00", steps: [
    { name: "claude", before: "2.1.285", after: "2.1.290", ok: true, note: "", freed: 0, kept: 0 },
    { name: "codex", before: "0.159.2", after: "0.159.2", ok: true, note: "", freed: 0, kept: 0 },
    { name: "agy", before: "1.2.14", after: "1.2.15", ok: true, note: "", freed: 209715200, kept: 0 },
    { name: "acg", before: "1.0.103", after: "1.0.104", ok: true, note: "", freed: 0, kept: 0 },
  ] },
  keepalive: { installed: true, times: ["07:00", "12:05", "17:10", "22:15"], model: "haiku", ccs: "",
    recent: ["2026-10-01 07:00 claude: ok"], window: { start: "07:00", reset: "12:00", drift: "" },
    tools: {
      claude: { installed: true, times: ["07:00", "12:05", "17:10", "22:15"], recent: [] },
      codex: { installed: true, times: ["08:00", "13:05"], recent: [] },
      agy: { installed: false, times: [], recent: [] },
    } },
};

const info = {
  get_info: [{
    version: "1.0.104", repo: "~/ai-config/data", provider: "git",
    build_commit: "7c1e2a9f00000000000000000000000000000000",
    tools: ["claude", "codex", "agy"], configured: true, config_error: "",
  }],
  settings_info: [{
    provider: "git", repo: "~/ai-config/data", remote_url: "git@github.com:you/ai-config-data.git",
    gdrive_space: "visible", gdrive_folder: "", gdrive_folder_url: "", signed_in: true,
  }],
};

const status = [
  "═══ Status: claude ═══",
  "✓ No differences found",
  "═══ Status: codex ═══",
  "~ skills/commit-helper/SKILL.md",
  "ℹ 1 file differs; run apply to deploy",
  "═══ Status: agy ═══",
  "✓ No differences found",
].join("\n");

test("首頁", async ({ page }) => {
  const run = { code: 0, output: status };
  await boot(page, { ...info, list_skills: [{ skills }], run: [run, run] });
  await page.locator("[data-cmd=status]").click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: out("home") });
});

test("記憶", async ({ page }) => {
  await boot(page, { ...info, list_skills: [{ skills }] });
  await queue(page, "memory_info", memory);
  await page.locator("#memory-open").click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: out("memory") });
});

test("自動化", async ({ page }) => {
  // 這頁在容器裡捲動,fullPage 拍不到下半;直接把視窗拉高
  await page.setViewportSize({ width: 960, height: 1180 });
  await boot(page, { ...info, list_skills: [{ skills }] });
  await queue(page, "memory_info", memory);
  await page.locator("#automation-open").click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: out("automation") });
});

test("技能", async ({ page }) => {
  await boot(page, { ...info, list_skills: [{ skills }, { skills }] });
  await page.locator("#package-open").click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: out("skills") });
});
