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
    const success = { code: 0, output: "✓ 完成", error: null, backup_path: null, recovery_required: false };
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
      cancel_preview: success,
      preview_apply: {
        ...success, token: "apply-first", needs_confirmation: true,
        scope: { tool: "codex", category: "settings" }, warnings: [],
        changes: [{ category: "settings", tool: "codex", operation: "modify",
          source: "/tmp/data/AGENTS.md", destination: "/tmp/tools/AGENTS.md",
          physical_target: "/tmp/tools/CLAUDE.md", shared: true, reason: "共用規則" }],
      },
      confirm_apply: success,
      preview_memory: {
        ...success, token: "memory-first", needs_confirmation: true,
        scope: { action: "enable" }, warnings: [],
        changes: [{ category: "memory", tool: "claude", operation: "create",
          source: null, destination: "/tmp/tools/CLAUDE.md",
          physical_target: null, shared: false, reason: "新增記憶入口" }],
      },
      confirm_memory: success,
      memory_info: {
        ...success, data_root: "/tmp/data/memory", shared_path: "/tmp/shared-memory",
        shared_status: "ok", tracked: true, git_status: "dirty", changed_paths: ["memory/MEMORY.md"],
        entries: [
          { tool: "claude", status: "installed", reason: "", path: "/tmp/CLAUDE.md", cli_installed: true },
          { tool: "codex", status: "blocked", reason: "override 遮蔽入口", path: "/tmp/AGENTS.md", cli_installed: false },
          { tool: "agy", status: "missing", reason: "", path: "/tmp/GEMINI.md", cli_installed: true },
        ],
        project: null,
        actions: {
          enable: { allowed: true, reason: "" }, disable: { allowed: true, reason: "" },
          adopt: { allowed: false, reason: "請先選擇專案" }, release: { allowed: false, reason: "請先選擇專案" },
          push: { allowed: true, reason: "" },
        },
        locations: [{ label: "全域記憶", path: "/tmp/data/memory", token: "location-global" }],
      },
      select_memory_project: {
        ...success, cancelled: true, project_token: null, root: null, key: null, stable: false,
      },
      open_memory_location: success,
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
