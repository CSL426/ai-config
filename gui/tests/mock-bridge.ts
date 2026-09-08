import { expect, type Page } from "@playwright/test";

type Reply = object;
type Call = { method: string; args: unknown[] };

declare global {
  interface Window {
    mockBridge: {
      calls: Call[];
      replies: Record<string, Reply[]>;
    };
  }
}

export const skills = Array.from({ length: 36 }, (_, index) => ({
  name: `skill-${String(index + 1).padStart(2, "0")}-${"long-name-".repeat(9)}end`,
  shared: index % 2 === 0,
  shareable: true,
}));

export async function boot(page: Page, replies: Record<string, Reply[]> = {}) {
  await page.addInitScript(({ entries, overrides }) => {
    const success = { code: 0, output: "✓ 完成" };
    const defaults: Record<string, Reply> = {
      get_info: {
        version: "1.0.0", repo: "/tmp/acg-test-data", provider: "gdrive",
        build_commit: "0123456789abcdef0123456789abcdef01234567",
        tools: ["claude", "codex", "agy"], configured: true, config_error: "",
      },
      settings_info: {
        provider: "gdrive", repo: "/tmp/acg-test-data", remote_url: "",
        gdrive_space: "visible", gdrive_folder: "test-config",
        gdrive_folder_url: "https://example.invalid/folder", signed_in: true,
      },
      list_skills: { skills: entries },
      run: {
        code: 0,
        output: "═══ Status: codex ═══\n✓ No differences found",
      },
      preview_push: {
        code: 0, output: "ℹ Configuration changes to commit:\n+ model = example",
        needs_confirmation: true, token: "preview-first",
      },
      confirm_push: success,
      package_skills: { ...success, zips: ["/tmp/acg-test-output/skill.zip"] },
      share_skills: success,
      unshare_skills: success,
      check_update: {
        code: 0, current: "1.0.0", latest: "1.0.0", up_to_date: true, output: "",
      },
      run_update: success,
      config_info: { code: 0, output: "provider: gdrive\nsigned in: yes" },
      open_data_dir: success,
      relogin_gdrive: { code: 0, output: "✓ Google 帳號已重新登入" },
      setup_gdrive: success,
      setup_repo: success,
    };
    window.mockBridge = { calls: [], replies: overrides };
    const api = Object.fromEntries(Object.entries(defaults).map(([method, fallback]) => [
      method,
      async (...args: unknown[]) => {
        window.mockBridge.calls.push({ method, args });
        const reply = window.mockBridge.replies[method]?.shift() ?? fallback;
        if ("reject" in reply) throw new Error(String(reply.reject));
        return reply;
      },
    ]));
    Object.assign(window, { pywebview: { api } });
  }, { entries: skills, overrides: replies });
  await page.goto("/");
  await expect(page.locator("[data-cmd=status]")).toBeEnabled();
}

export async function queue(page: Page, method: string, ...replies: Reply[]) {
  await page.evaluate(({ method, replies }) => {
    window.mockBridge.replies[method] = replies;
  }, { method, replies });
}

export async function calls(page: Page, method: string) {
  return page.evaluate((method) =>
    window.mockBridge.calls.filter((call) => call.method === method), method);
}
