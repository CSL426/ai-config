import { expect, test } from "@playwright/test";
import { boot, calls, queue } from "./mock-bridge";

// 畫面用本機時間;固定時區,測試才不會隨執行的機器改變
test.use({ timezoneId: "UTC" });

const success = { code: 0, output: "完成", error: null, backup_path: null, recovery_required: false };

const base = {
  ...success, data_root: "/tmp/data/memory", shared_path: "/tmp/shared-memory",
  shared_status: "ok", tracked: true, git_status: "clean", changed_paths: [], entries: [],
  project: null, locations: [],
  index_unlisted: [], index_dangling: [], secret_notes: [],
  actions: {
    enable: { allowed: true, reason: "" }, disable: { allowed: true, reason: "" },
    adopt: { allowed: false, reason: "" },
    release: { allowed: true, reason: "" }, push: { allowed: true, reason: "" },
  },
  autoupdate: { installed: false, time: "", last_run: "", steps: [] },
};

const ran = {
  installed: true, time: "05:30", last_run: "2026-09-30T05:31:00+00:00",
  steps: [
    { name: "claude", before: "2.1.285", after: "2.1.286", ok: true, note: "", freed: 209715200, kept: 2 },
    { name: "agy", before: "1.2.14", after: "1.2.14", ok: false, note: "Update failed: network" },
    { name: "acg", before: "1.0.101", after: "1.0.101", ok: true, note: "" },
  ],
};

async function openAutomation(page: Parameters<typeof boot>[0], info: object) {
  await queue(page, "memory_info", info);
  await page.locator("#automation-open").click();
}

test("沒排定時開關是關的,也不顯示結果", async ({ page }) => {
  await boot(page);
  await openAutomation(page, base);

  await expect(page.locator("#autoupdate-toggle")).not.toBeChecked();
  await expect(page.locator("#autoupdate-steps-row")).toBeHidden();
  await expect(page.locator("#autoupdate-failure")).toBeHidden();
});

test("已排定時顯示時間與每個工具上次的版本", async ({ page }) => {
  await boot(page);
  await openAutomation(page, { ...base, autoupdate: ran });

  await expect(page.locator("#autoupdate-toggle")).toBeChecked();
  await expect(page.locator("#autoupdate-hint")).toContainText("每晚 05:30 先更新");
  const steps = page.locator("#autoupdate-steps");
  await expect(steps).toContainText("2026-09-30 05:31");
  await expect(steps).toContainText("2.1.285 → 2.1.286（清掉舊執行檔 200 MB）（2 個舊執行檔使用中，下次再清）");
  await expect(steps).toContainText("1.0.101 已是最新");
});

test("有工具沒更新成功時說出是哪個與原因", async ({ page }) => {
  await boot(page);
  await openAutomation(page, { ...base, autoupdate: ran });

  const failure = page.locator("#autoupdate-failure");
  await expect(failure).toBeVisible();
  await expect(failure).toContainText("agy Update failed: network");
  await expect(failure).not.toContainText("claude");
});

test("打開開關會請後端排定", async ({ page }) => {
  await boot(page);
  await openAutomation(page, base);
  await queue(page, "memory_info", { ...base, autoupdate: { ...ran, steps: [] } });

  await page.locator("#autoupdate-toggle").click();
  await expect.poll(async () => (await calls(page, "set_autoupdate")).at(-1)?.args).toEqual([true]);
});

test("只開自動更新時,每晚排程的時間仍然可以改", async ({ page }) => {
  await boot(page);
  await openAutomation(page, {
    ...base,
    autopush: { installed: false, scheduled: true, last_push: "", reason: "",
      slot: "04:20", host: "workstation", others: [] },
    autoupdate: { ...ran, time: "04:20" },
  });

  await expect(page.locator("#autopush-toggle")).not.toBeChecked();
  await expect(page.locator("#autopush-slot-row")).toBeVisible();
  await expect(page.locator("#autopush-slot")).toHaveValue("04:20");
});

test("後端失敗時開關退回原狀", async ({ page }) => {
  await boot(page);
  await openAutomation(page, base);
  await queue(page, "set_autoupdate", { ...success, code: 1, output: "啟用 timer 失敗" });

  await page.locator("#autoupdate-toggle").click();

  await expect(page.locator("#autoupdate-toggle")).not.toBeChecked();
  await expect(page.locator("#automation-feedback")).toContainText("啟用 timer 失敗");
});

test("舊版後端沒有這個欄位時不會壞掉", async ({ page }) => {
  const { autoupdate, ...legacy } = base;
  await boot(page);
  await openAutomation(page, legacy);

  await expect(page.locator("#autoupdate-toggle")).not.toBeChecked();
  await expect(page.locator("#autopush-toggle")).not.toBeChecked();
});
