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
  autopush: { installed: false, last_push: "", reason: "",
    slot: "", host: "gpu-a4000", others: [] },
};

async function openMemory(page: Parameters<typeof boot>[0], info: object) {
  await queue(page, "memory_info", info);
  await page.locator("#automation-open").click();
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
    autopush: { installed: true, last_push: "2026-09-14T04:00:00+00:00", reason: "",
      slot: "04:00", host: "gpu-a4000", others: [] },
  });

  await expect(page.locator("#autopush-toggle")).toBeChecked();
  await expect(page.locator("#autopush-hint")).toContainText("2026-09-14 04:00");
});

test("上次排程沒推成時說出原因與檔案,成功後消失", async ({ page }) => {
  await boot(page);
  await openMemory(page, {
    ...base,
    autopush: { installed: true, last_push: "2026-09-29T04:11:00+00:00", reason: "",
      last_failure: { when: "2026-09-30T04:18:00+00:00",
        reason: "Potential credential content would be committed; push cancelled:",
        paths: ["memory/handoff/acg 排程.md"] },
      slot: "04:18", host: "gpu-a4000", others: [] },
  });

  const failure = page.locator("#autopush-failure");
  await expect(failure).toBeVisible();
  await expect(failure).toContainText("2026-09-30 04:18");
  await expect(failure).toContainText("memory/handoff/acg 排程.md");

  await queue(page, "memory_info", { ...base, autopush: { installed: true,
    last_push: "2026-09-30T09:54:00+00:00", reason: "", last_failure: null,
    slot: "04:18", host: "gpu-a4000", others: [] } });
  await page.locator("#automation-refresh").click();
  await expect(failure).toBeHidden();
});

test("打開開關會請後端排定", async ({ page }) => {
  await boot(page);
  await openMemory(page, base);
  await queue(page, "memory_info", { ...base, autopush: { installed: true, last_push: "", reason: "",
    slot: "04:00", host: "gpu-a4000", others: [] } });

  await page.locator("#autopush-toggle").click();

  expect((await calls(page, "set_autopush")).at(-1)?.args).toEqual([true]);
});

test("後端失敗時開關退回原狀", async ({ page }) => {
  await boot(page);
  await openMemory(page, base);
  await queue(page, "set_autopush", { ...success, code: 1, output: "沒有 systemd" });

  await page.locator("#autopush-toggle").click();

  await expect(page.locator("#autopush-toggle")).not.toBeChecked();
  await expect(page.locator("#automation-feedback")).toContainText("沒有 systemd");
});

test("舊版後端沒有這個欄位時不會壞掉", async ({ page }) => {
  const { autopush, ...legacy } = base;
  await boot(page);
  await openMemory(page, legacy);

  await expect(page.locator("#autopush-toggle")).not.toBeChecked();
  await expect(page.locator("#memory-summary")).not.toBeEmpty();
});


test("沒排定時不顯示時間欄位", async ({ page }) => {
  await boot(page);
  await openMemory(page, base);

  await expect(page.locator("#autopush-slot-row")).toBeHidden();
});

test("已排定時顯示這台的時間與其他機器", async ({ page }) => {
  await boot(page);
  await openMemory(page, {
    ...base,
    autopush: {
      installed: true, last_push: "", reason: "",
      slot: "04:20", host: "gpu-a4000",
      others: [{ host: "gn100-d091", slot: "04:00" }],
    },
  });

  await expect(page.locator("#autopush-slot")).toHaveValue("04:20");
  await expect(page.locator("#autopush-others")).toContainText("gn100-d091 04:00");
});

test("只有一台時說明會自動錯開", async ({ page }) => {
  await boot(page);
  await openMemory(page, {
    ...base,
    autopush: { installed: true, last_push: "", reason: "",
      slot: "04:00", host: "gpu-a4000", others: [] },
  });

  await expect(page.locator("#autopush-others")).toContainText("只有這台");
});

test("改時間會送到後端", async ({ page }) => {
  await boot(page);
  await openMemory(page, {
    ...base,
    autopush: { installed: true, last_push: "", reason: "",
      slot: "04:00", host: "gpu-a4000", others: [] },
  });
  await queue(page, "memory_info", base);

  await page.locator("#autopush-slot").fill("05:30");
  await page.locator("#autopush-slot-save").click();

  expect((await calls(page, "set_autopush_slot")).at(-1)?.args).toEqual(["05:30"]);
});
