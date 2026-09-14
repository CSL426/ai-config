import { expect, test } from "@playwright/test";
import { boot, queue } from "./mock-bridge";

const success = { code: 0, output: "完成", error: null, backup_path: null, recovery_required: false };

const base = {
  ...success, data_root: "/tmp/data/memory", shared_path: "/tmp/shared-memory",
  shared_status: "ok", tracked: true, git_status: "clean", changed_paths: [], entries: [],
  project: null,
  actions: {
    enable: { allowed: true, reason: "" }, disable: { allowed: true, reason: "" },
    adopt: { allowed: false, reason: "" },
    release: { allowed: true, reason: "" }, push: { allowed: true, reason: "" },
  },
  locations: [],
  index_unlisted: [], index_dangling: [], secret_notes: [],
};

async function openMemory(page: Parameters<typeof boot>[0], info: object) {
  await queue(page, "memory_info", info);
  await page.locator("#memory-open").click();
}

test("乾淨的記憶不顯示健康區塊", async ({ page }) => {
  await boot(page);
  await openMemory(page, base);

  await expect(page.locator("#memory-health")).toBeHidden();
});

test("疑似憑證的筆記以錯誤樣式列出", async ({ page }) => {
  await boot(page);
  await openMemory(page, { ...base, secret_notes: ["topics/deploy.md"] });

  const health = page.locator("#memory-health");
  await expect(health).toBeVisible();
  await expect(health).toContainText("疑似含有憑證的筆記（1）");
  await expect(health).toContainText("topics/deploy.md");
  await expect(health.locator("[data-kind=error]")).toHaveCount(1);
});

test("索引漂移的兩種狀況分開列出", async ({ page }) => {
  await boot(page);
  await openMemory(page, {
    ...base,
    index_unlisted: ["topics/gotchas.md"],
    index_dangling: ["topics/removed.md"],
  });

  const health = page.locator("#memory-health");
  await expect(health).toContainText("沒有寫進索引的筆記（1）");
  await expect(health).toContainText("指向不存在檔案的索引連結（1）");
  await expect(health.locator("[data-kind=warn]")).toHaveCount(2);
});

test("舊版後端沒有這些欄位時不會讓整頁停擺", async ({ page }) => {
  const { index_unlisted, index_dangling, secret_notes, ...legacy } = base;
  await boot(page);
  await openMemory(page, legacy);

  await expect(page.locator("#memory-health")).toBeHidden();
  await expect(page.locator("#memory-summary")).not.toBeEmpty();
});

test("長路徑在窄視窗不溢出", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 720 });
  await boot(page);
  await openMemory(page, {
    ...base,
    secret_notes: ["topics/" + "very-long-note-name".repeat(6) + ".md"],
  });

  const list = page.locator(".memory-health-list");
  await expect(list).toBeVisible();
  const overflow = await list.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
