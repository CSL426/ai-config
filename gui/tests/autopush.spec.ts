import { expect, test } from "@playwright/test";
import { boot, calls, queue } from "./mock-bridge";

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
  autopush: { installed: false, last_push: "", reason: "" },
};

async function openMemory(page: Parameters<typeof boot>[0], info: object) {
  await queue(page, "memory_info", info);
  await page.locator("#memory-open").click();
}

test("沒排定時開關是關的", async ({ page }) => {
  await boot(page);
  await openMemory(page, base);

  await expect(page.locator("#autopush-toggle")).not.toBeChecked();
});

test("已排定時開關是開的,並顯示上次上傳時間", async ({ page }) => {
  await boot(page);
  await openMemory(page, {
    ...base,
    autopush: { installed: true, last_push: "2026-09-14T04:00:00+00:00", reason: "" },
  });

  await expect(page.locator("#autopush-toggle")).toBeChecked();
  await expect(page.locator("#autopush-hint")).toContainText("2026-09-14 04:00");
});

test("打開開關會請後端排定", async ({ page }) => {
  await boot(page);
  await openMemory(page, base);
  await queue(page, "memory_info", { ...base, autopush: { installed: true, last_push: "", reason: "" } });

  await page.locator("#autopush-toggle").click();

  expect((await calls(page, "set_autopush")).at(-1)?.args).toEqual([true]);
});

test("後端失敗時開關退回原狀", async ({ page }) => {
  await boot(page);
  await openMemory(page, base);
  await queue(page, "set_autopush", { ...success, code: 1, output: "沒有 systemd" });

  await page.locator("#autopush-toggle").click();

  await expect(page.locator("#autopush-toggle")).not.toBeChecked();
  await expect(page.locator("#memory-feedback")).toContainText("沒有 systemd");
});

test("舊版後端沒有這個欄位時不會壞掉", async ({ page }) => {
  const { autopush, ...legacy } = base;
  await boot(page);
  await openMemory(page, legacy);

  await expect(page.locator("#autopush-toggle")).not.toBeChecked();
  await expect(page.locator("#memory-summary")).not.toBeEmpty();
});
