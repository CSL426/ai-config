/** Getting connected and staying connected: first-run setup, the GitHub
 * login that lets a push succeed, and the settings dialog that edits both.
 *
 * These three call each other in a ring — a refused push offers the
 * settings dialog, settings reads GitHub access, and switching provider
 * re-runs setup — so they share a file rather than pretending to be
 * separable.
 */

import {
  $, appNotice, githubAccounts, githubActions, githubCode, githubCodeCopy,
  githubCodeHint, githubCodeValue, githubFallback, githubGroup, githubLoginBtn,
  githubState, providerEl, repoEl, settingsBox, settingsFeedback,
  settingsClose, settingsRetry, settingsSwitch, settingsSwitchForm,   setupGdrivePanel,
  setupGitPanel, versionEl,
} from "./dom";
import { closeActiveSelect } from "./select";
import { state } from "./state";
import {
  api, copyText, feedback, firstErrorLine, goBack, openOutput, perform, showView, syncControls, } from "./shell";
import type { GithubAccess } from "./bridge";

let requestBoot: () => Promise<void> = async () => {};
export function setRequestBoot(handler: () => Promise<void>): void {
  requestBoot = handler;
}

export function field(panel: HTMLElement, name: string): HTMLInputElement {
  const input = panel.querySelector<HTMLInputElement>(`[data-field="${name}"]`);
  if (!input) throw new Error(`missing field: ${name}`);
  return input;
}

export function bindSpace(panel: HTMLElement): void {
  const sync = (): void => {
    const folder = panel.querySelector<HTMLElement>('[data-field-row="gdrive-folder"]');
    if (folder) folder.hidden = panel.querySelector<HTMLInputElement>('[data-field="gdrive-space"]:checked')?.value === "hidden";
  };
  for (const radio of panel.querySelectorAll<HTMLInputElement>('[data-field="gdrive-space"]')) {
    radio.addEventListener("change", sync);
  }
  sync();
}
bindSpace(setupGdrivePanel);
for (const radio of document.querySelectorAll<HTMLInputElement>('input[name="setup-provider"]')) {
  radio.addEventListener("change", () => {
    if (!radio.checked) return;
    setupGitPanel.hidden = radio.value !== "git";
    setupGdrivePanel.hidden = radio.value !== "gdrive";
  });
}

export async function submitSetup(provider: string, panel: HTMLElement, switching = false): Promise<void> {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview) return;
  if (provider === "git" && !field(panel, "repo-url").value.trim()) {
    const input = field(panel, "repo-url");
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", switching ? "settings-feedback" : "app-notice");
    feedback(switching ? settingsFeedback : appNotice, "請先填入儲存庫 Git URL。", true);
    input.focus();
    return;
  }
  panel.querySelector('[aria-invalid="true"]')?.removeAttribute("aria-invalid");
  const dataDir = field(panel, "data-dir").value;
  const repoUrl = provider === "git" ? field(panel, "repo-url").value : "";
  // 私有 HTTPS 儲存庫需要綁一個 gh 已登入的帳號;切換表單是複製來的,欄位可能不存在
  const account = provider === "git"
    ? (panel.querySelector<HTMLInputElement>('[data-field="account"]')?.value ?? "")
    : "";
  const folder = provider === "gdrive" ? field(panel, "gdrive-folder").value : "";
  const space = panel.querySelector<HTMLInputElement>('[data-field="gdrive-space"]:checked')?.value ?? "visible";
  if (switching) showSettings(false);
  const task = () =>
    provider === "git"
      ? bridge.setup_repo(repoUrl, dataDir, account)
      : bridge.setup_gdrive(dataDir, folder, space);
  const result = await perform(switching ? "切換同步方式" : "首次設定", task);
  if (result?.code === 0) {
    // 後端工具路徑於啟動時載入；完成 setup 後不可使用舊程序繼續同步。
    state.restartRequired = true;
    feedback(appNotice, "設定完成。請關閉並重新開啟視窗，載入新的連線設定。");
    syncControls();
  }
}
$("#setup-go").addEventListener("click", () => { void submitSetup("git", setupGitPanel); });
$("#setup-gdrive-go").addEventListener("click", () => { void submitSetup("gdrive", setupGdrivePanel); });

export async function loadInfo(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  try {
    const info = await bridge.get_info();
    state.connected = true;
    state.configured = info.configured;
    versionEl.textContent = info.build_commit
      ? `v${info.version} · ${info.build_commit.slice(0, 8)}`
      : `v${info.version}`;
    versionEl.title = info.build_commit || "";
    providerEl.textContent = state.configured ? providerLabel(info.provider) : "尚未設定同步方式";
    providerEl.dataset.provider = state.configured ? info.provider : "none";
    repoEl.textContent = `本機設定位置：${info.repo}`;
    repoEl.title = info.repo;
    field(setupGitPanel, "data-dir").value = info.repo;
    field(setupGdrivePanel, "data-dir").value = info.repo;
    feedback(appNotice, info.config_error ? `設定檔有問題：${info.config_error}` : "", Boolean(info.config_error));
    $("#info-retry").hidden = true;
    showView(state.currentView, false);
  } catch (error) {
    state.connected = false;
    feedback(appNotice, `無法讀取連線資訊：${String(error)}`, true);
    $("#info-retry").hidden = false;
  } finally {
    syncControls();
  }
}
$("#info-retry").addEventListener("click", () => { void requestBoot(); });

// 後端在 pull／push 被私有儲存庫拒絕時會印出這句;看到就給一顆直接去登入的按鈕
export const REFUSED_MARKER = "遠端拒絕存取";

export function offerLogin(output: string): void {
  if (!output.includes(REFUSED_MARKER)) return;
  feedback(appNotice, "遠端拒絕存取：這台還沒有能讀取資料儲存庫的帳號。", true);
  const button = document.createElement("button");
  button.className = "btn btn-primary";
  button.textContent = "前往設定登入";
  button.addEventListener("click", () => { showSettings(true); });
  appNotice.append(document.createTextNode(" "), button);
}

export function providerLabel(provider: string): string {
  return provider === "gdrive" ? "Google Drive" : "私人 Git 儲存庫";
}

// ── GitHub 上傳權限 ─────────────────────

let githubPollTimer: number | null = null;

export function stopGithubPolling(): void {
  if (githubPollTimer !== null) {
    window.clearTimeout(githubPollTimer);
    githubPollTimer = null;
  }
}

export function resetGithubCode(): void {
  stopGithubPolling();
  githubCode.hidden = true;
  githubCodeValue.textContent = "————";
  githubCodeHint.textContent = "";
  githubLoginBtn.disabled = false;
  githubLoginBtn.textContent = "用瀏覽器登入 GitHub";
}

export async function loadGithubAccess(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  resetGithubCode();
  githubGroup.hidden = false;
  githubState.textContent = "檢查中…";
  githubAccounts.hidden = true;
  githubAccounts.replaceChildren();
  githubActions.hidden = true;

  let access: GithubAccess;
  try {
    access = await bridge.github_access();
  } catch {
    githubGroup.hidden = true;
    return;
  }

  // 遠端不是 GitHub 就沒什麼好說的,整段收起來
  if (!access.repository) {
    githubGroup.hidden = true;
    return;
  }

  githubState.textContent = access.lines.join(" ");
  if (access.can_push === true) return;

  // 已經登入過的其他帳號通常就是解法,直接讓人一鍵切過去
  const others = access.accounts.filter(
    (name: string) => name !== access.account,
  );
  if (others.length > 0) {
    for (const name of others) {
      const button = document.createElement("button");
      button.className = "btn btn-ghost";
      button.textContent = `改用 ${name}`;
      button.addEventListener("click", () => void useGithubAccount(name));
      githubAccounts.append(button);
    }
    githubAccounts.hidden = false;
  }
  githubActions.hidden = !access.installed || !access.device_login;
  // 沒有內建瀏覽器登入的版本:給一顆真的按鈕,替使用者開終端機跑 gh 的登入
  githubFallback.hidden = !access.installed || access.device_login;
}

async function terminalGithubLogin(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  githubState.textContent = "正在開啟終端機…";
  const result = await bridge.github_terminal_login();
  githubState.textContent = result.output;
}

async function useGithubAccount(account: string): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  githubState.textContent = `正在切換到 ${account}…`;
  const result = await bridge.github_use_account(account);
  githubState.textContent = result.output;
  if (result.code === 0) void loadGithubAccess();
}

async function startGithubLogin(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  githubLoginBtn.disabled = true;
  githubLoginBtn.textContent = "準備中…";

  const start = await bridge.github_start_login();
  if (start.code !== 0 || !start.device_code) {
    githubState.textContent = start.output || "無法開始登入";
    resetGithubCode();
    return;
  }

  githubCodeValue.textContent = start.user_code ?? "";
  githubCodeHint.textContent = `已開啟 ${start.verification_uri}，完成後這裡會自動更新`;
  githubCode.hidden = false;
  githubLoginBtn.textContent = "等待瀏覽器確認…";

  const interval = Math.max(start.interval ?? 5, 1);
  const deadline = Date.now() + 15 * 60 * 1000;

  const poll = async (): Promise<void> => {
    if (Date.now() > deadline) {
      githubState.textContent = "登入逾時，請再試一次";
      resetGithubCode();
      return;
    }
    const result = await bridge.github_poll_login(start.device_code!, interval);
    if (result.status === "pending") {
      githubPollTimer = window.setTimeout(() => void poll(), interval * 1000);
      return;
    }
    resetGithubCode();
    githubState.textContent = result.output;
    if (result.status === "done") void loadGithubAccess();
  };

  githubPollTimer = window.setTimeout(() => void poll(), interval * 1000);
}

githubLoginBtn.addEventListener("click", () => void startGithubLogin());
$("#github-terminal-login").addEventListener("click", () => void terminalGithubLogin());
$("#github-recheck").addEventListener("click", () => void loadGithubAccess());

githubCodeCopy.addEventListener("click", async () => {
  const ok = await copyText(githubCodeValue.textContent ?? "");
  githubCodeCopy.textContent = ok ? "已複製！" : "請手動複製";
  window.setTimeout(() => {
    githubCodeCopy.textContent = "複製";
  }, 2000);
});

export async function loadSettings(): Promise<void> {
  const bridge = api();
  if (!bridge || state.settingsLoading) return;
  state.settingsLoading = true;
  state.settingsInfo = null;
  settingsSwitch.hidden = true;
  settingsRetry.hidden = true;
  feedback(settingsFeedback, "讀取設定中…");
  syncControls();
  try {
    const info = await bridge.settings_info();
    state.settingsInfo = info;
    for (const radio of settingsBox.querySelectorAll<HTMLInputElement>('input[name="settings-provider"]')) {
      radio.checked = radio.value === info.provider;
      const badge = radio.closest(".provider-choice")?.querySelector<HTMLElement>(".provider-choice-current");
      if (badge) badge.hidden = !radio.checked;
    }
    $("#settings-row-account").hidden = info.provider !== "gdrive";
    $("#settings-row-folder").hidden = info.provider !== "gdrive";
    $("#settings-row-remote").hidden = info.provider === "gdrive" || !info.remote_url;
    $("#settings-account").textContent = info.signed_in ? "已授權" : "尚未登入";
    $("#settings-folder").textContent = info.gdrive_space === "hidden"
      ? "隱藏的應用程式空間" : `我的雲端硬碟 / ${info.gdrive_folder}`;
    $("#settings-folder").title = $("#settings-folder").textContent ?? "";
    $("#settings-folder-open").hidden = !info.gdrive_folder_url;
    $("#settings-folder-open").dataset.url = info.gdrive_folder_url;
    $("#settings-remote").textContent = info.remote_url || "—";
    $("#settings-repo").textContent = info.repo;
    feedback(settingsFeedback, "");
    // 只有 git provider 才談得上 GitHub 推送權限
    if (info.provider === "gdrive") {
      githubGroup.hidden = true;
      resetGithubCode();
    } else {
      void loadGithubAccess();
    }
  } catch (error) {
    feedback(settingsFeedback, `無法讀取設定：${String(error)}`, true);
    settingsRetry.hidden = false;
  } finally {
    state.settingsLoading = false;
    syncControls();
  }
}

export function showSettings(show: boolean): void {
  settingsBox.hidden = !show;
  // 關閉時停止輪詢並清掉畫面:否則離開後還在打 GitHub 的 API,
  // 而且下次打開會看到一組早就失效的舊驗證碼
  if (!show) resetGithubCode();
  for (const child of Array.from($(".app").children)) {
    if (child instanceof HTMLElement && child !== settingsBox) child.inert = show;
  }
  if (show) {
    state.settingsOpener = document.activeElement as HTMLElement | null;
    settingsClose.focus();
    void loadSettings();
  } else {
    state.settingsOpener?.focus();
    state.settingsOpener = null;
  }
}
$("#settings-open").addEventListener("click", () => { showSettings(true); });
settingsClose.addEventListener("click", () => { showSettings(false); });
settingsRetry.addEventListener("click", () => { void loadSettings(); });
settingsBox.addEventListener("click", event => {
  if (event.target === settingsBox) showSettings(false);
});
document.addEventListener("keydown", event => {
  if (event.defaultPrevented || event.isComposing) return;
  if (event.key === "Escape") {
    event.preventDefault();
    if (event.repeat || closeActiveSelect()) return;
    if (!settingsBox.hidden) showSettings(false);
    else goBack();
    return;
  }
  if (settingsBox.hidden) return;
  if (event.key === "Tab") {
    const focusable = Array.from(settingsBox.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
    )).filter(element => element.getClientRects().length > 0);
    const first = focusable[0];
    const last = focusable.at(-1);
    const outside = !settingsBox.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || outside)) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && (document.activeElement === last || outside)) {
      event.preventDefault();
      first?.focus();
    }
  }
});
$("#settings-open-dir").addEventListener("click", async () => {
  const bridge = api();
  if (!bridge) return;
  const result = await perform("開啟資料夾", () => bridge.open_data_dir(), false);
  if (result) feedback(settingsFeedback, result.code === 0 ? "已開啟資料夾。" : firstErrorLine(result.output), result.code !== 0);
});
$("#settings-folder-open").addEventListener("click", () => {
  const url = $("#settings-folder-open").dataset.url;
  if (url) window.open(url, "_blank", "noopener,noreferrer");
});
$("#settings-relogin").addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || state.settingsInfo?.provider !== "gdrive") return;
  feedback(settingsFeedback, "請在瀏覽器完成 Google 登入。儲存位置保持不變。");
  const result = await perform("重新登入 Google", () => bridge.relogin_gdrive(), false);
  if (!result) return;
  if (result.code === 0) await loadSettings();
  feedback(settingsFeedback, result.code === 0 ? "已重新登入，儲存位置保持不變。" : firstErrorLine(result.output), result.code !== 0);
  if (settingsBox.hidden) openOutput();
});

export function offerProviderSwitch(provider: string): void {
  const info = state.settingsInfo;
  if (!info) return;
  settingsSwitch.hidden = false;
  feedback(settingsFeedback, "");
  $("#settings-switch-note").textContent = `切換到${providerLabel(provider)}需要設定新的連線。既有資料不會刪除，兩邊的資料也不會自動搬移。`;
  const source = provider === "gdrive" ? setupGdrivePanel : setupGitPanel;
  const panel = source.cloneNode(true) as HTMLElement;
  panel.hidden = false;
  panel.removeAttribute("id");
  for (const node of panel.querySelectorAll("[id]")) node.removeAttribute("id");
  // 複製表單的 radio 必須獨立分組，避免改到仍存在的首次設定表單。
  for (const radio of panel.querySelectorAll<HTMLInputElement>('input[type="radio"]')) {
    radio.name = `switch-${radio.name}`;
    if (radio.dataset.field === "gdrive-space") radio.checked = radio.value === info.gdrive_space;
  }
  field(panel, "data-dir").value = info.repo;
  if (provider === "gdrive") field(panel, "gdrive-folder").value = info.gdrive_folder || "ai-config";
  settingsSwitchForm.replaceChildren(panel);
  bindSpace(panel);
  const go = panel.querySelector<HTMLButtonElement>(".btn-primary")!;
  go.addEventListener("click", () => { void submitSetup(provider, panel, true); });
  syncControls();
  settingsSwitch.scrollIntoView({ block: "nearest" });
}
for (const radio of settingsBox.querySelectorAll<HTMLInputElement>('input[name="settings-provider"]')) {
  radio.addEventListener("change", () => {
    if (!radio.checked) return;
    if (radio.value === state.settingsInfo?.provider) settingsSwitch.hidden = true;
    else offerProviderSwitch(radio.value);
  });
}

$("#package-open").addEventListener("click", () => { showView("skills"); });
$("#package-back").addEventListener("click", goBack);
$("#output-toggle").addEventListener("click", openOutput);
$("#skill-output").addEventListener("click", openOutput);
$("#output-back").addEventListener("click", goBack);
$("#export-back").addEventListener("click", goBack);

