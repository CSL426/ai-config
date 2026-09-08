import "./style.css";

import type {
  AcgApi, AcgCommand, GithubAccess, RunResult, SettingsInfo, SkillEntry,
} from "./bridge";

const COMMAND_LABELS: Record<AcgCommand, string> = {
  status: "檢查狀態", apply: "套用設定", pull: "下載更新", push: "上傳變更",
};
const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`missing element: ${selector}`);
  return element;
};
const versionEl = $("#version");
const repoEl = $("#repo");
const providerEl = $("#provider");
const settingsBox = $("#settings");
const settingsClose = $<HTMLButtonElement>("#settings-close");
const settingsFeedback = $("#settings-feedback");
const settingsRetry = $<HTMLButtonElement>("#settings-retry");
const settingsSwitch = $("#settings-switch");
const settingsSwitchForm = $("#settings-switch-form");
const githubGroup = $("#settings-github");
const githubState = $("#github-state");
const githubAccounts = $("#github-accounts");
const githubActions = $("#github-actions");
const githubLoginBtn = $<HTMLButtonElement>("#github-login");
const githubCode = $("#github-code");
const githubCodeValue = $("#github-code-value");
const githubCodeCopy = $<HTMLButtonElement>("#github-code-copy");
const githubCodeHint = $("#github-code-hint");
const toolTabs = Array.from(document.querySelectorAll<HTMLButtonElement>(".scope-tab"));
const toolRows = Array.from(document.querySelectorAll<HTMLElement>(".tool-row"));
const heroMark = $("#hero-mark");
const heroTitle = $("#hero-title");
const heroSub = $("#hero-sub");
const confirmBox = $("#confirm");
const confirmYes = $<HTMLButtonElement>("#confirm-yes");
const outputTitle = $("#output-title");
const outputState = $("#output-state");
const outputBody = $("#output-body");
const outputCopy = $<HTMLButtonElement>("#output-copy");
const copyFallback = $<HTMLTextAreaElement>("#copy-fallback");
const skillList = $("#skill-list");
const skillSearch = $<HTMLInputElement>("#skill-search");
const skillFilter = $<HTMLSelectElement>("#skill-filter");
const skillResult = $("#skill-result");
const skillRetry = $<HTMLButtonElement>("#skill-retry");
const packageMessage = $<HTMLTextAreaElement>("#package-message");
const packageCopy = $<HTMLButtonElement>("#package-copy");
const updateBtn = $<HTMLButtonElement>("#update-check");
const appNotice = $("#app-notice");
const operationStatus = $("#operation-status");
const setupBox = $("#setup");
const setupGitPanel = $("#setup-git-panel");
const setupGdrivePanel = $("#setup-gdrive-panel");

type MainView = "status" | "output" | "skills" | "export";
let currentView: MainView = "status";
let outputReturn: MainView = "status";
let selectedTool = "all";
let configured = false;
let running = false;
let connected = false;
let restartRequired = false;
let skillsLoading = false;
let settingsLoading = false;
let pendingPushToken: string | null = null;
let pendingPushTool = "all";
let pendingUpdate: string | null = null;
let updateInstalled = false;
let settingsInfo: SettingsInfo | null = null;
let settingsOpener: HTMLElement | null = null;
let skills: SkillEntry[] = [];
const selectedSkills = new Set<string>();

function api(): AcgApi | null {
  return window.pywebview?.api ?? null;
}

function toolLabel(tool = selectedTool): string {
  return toolTabs.find(tab => tab.dataset.tool === tool)?.textContent?.trim() ?? tool;
}

function feedback(element: HTMLElement, text: string, failed = false): void {
  element.textContent = text;
  element.hidden = !text;
  element.classList.toggle("is-fail", failed);
}

function syncControls(): void {
  const blocked = running || !connected || pendingPushToken !== null;
  for (const control of document.querySelectorAll<
    HTMLButtonElement | HTMLInputElement | HTMLSelectElement
  >("button, input, select")) {
    control.disabled = blocked;
  }
  // 閱讀、複製、取消確認與關閉對話框不會更動設定。
  for (const id of ["#output-copy", "#package-copy", "#settings-close"]) {
    $<HTMLButtonElement>(id).disabled = false;
  }
  for (const id of ["#output-back", "#confirm-yes", "#confirm-no"]) {
    $<HTMLButtonElement>(id).disabled = running;
  }
  $<HTMLButtonElement>("#info-retry").disabled = running;
  updateBtn.disabled = blocked || updateInstalled;
  for (const control of document.querySelectorAll<HTMLButtonElement>(
    "[data-cmd], .scope-tab, #package-open",
  )) control.disabled = blocked || !configured || restartRequired;
  for (const control of setupBox.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
    "input, button",
  )) control.disabled = blocked || restartRequired;
  for (const control of settingsBox.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
    "input, button:not(#settings-close):not(#settings-retry)",
  )) control.disabled = blocked || settingsLoading || settingsInfo === null;
  settingsRetry.disabled = blocked || settingsLoading;
  $<HTMLButtonElement>("#settings-open").disabled = blocked || restartRequired;
  const selected = skills.filter((skill) => selectedSkills.has(skill.name));
  const unavailable = blocked || !configured || skillsLoading || restartRequired;
  const hasSelection = selected.length > 0;
  const canShare = hasSelection && selected.every((skill) => skill.shareable);
  const canUnshare = hasSelection && selected.every((skill) => skill.shared);

  $<HTMLButtonElement>("#skill-share").disabled = unavailable || !canShare;
  $<HTMLButtonElement>("#skill-unshare").disabled = unavailable || !canUnshare;
  $<HTMLButtonElement>("#skill-package").disabled = unavailable || !hasSelection;
  $<HTMLButtonElement>("#skill-all").disabled = unavailable || visibleSkills().length === 0;
  $<HTMLButtonElement>("#skill-none").disabled = unavailable || !hasSelection;
  skillSearch.disabled = blocked || skillsLoading;
  skillFilter.disabled = blocked || skillsLoading;
  skillRetry.disabled = blocked || skillsLoading;
  $("#skill-selection").textContent = `已選 ${selected.length} 項 · 顯示 ${visibleSkills().length} / ${skills.length} 項`;
  $("#skill-share").title = !hasSelection || canShare
    ? "分享給 Codex 與 Antigravity"
    : "選取項目包含無法從 Claude Code 分享的技能";
  $("#skill-unshare").title = !hasSelection || canUnshare
    ? "收回已分享的技能"
    : "請只選擇已分享的技能";
}

function setBusy(busy: boolean, label = ""): void {
  running = busy;
  feedback(operationStatus, busy ? `${label}，請稍候…` : "");
  $(".app").setAttribute("aria-busy", String(busy));
  syncControls();
}

function cancelPush(): void {
  pendingPushToken = null;
  confirmBox.hidden = true;
  syncControls();
}

function viewFocusTarget(view: MainView): HTMLElement {
  switch (view) {
    case "output":
      return outputTitle;
    case "skills":
      return $("#skills-title");
    case "export":
      return $("#export-title");
    case "status":
      return configured ? heroTitle : $(".setup-title");
  }
}

function showView(view: MainView, focus = true): void {
  if (view !== "output" && pendingPushToken) {
    cancelPush();
    outputState.textContent = "已取消確認，尚未上傳";
    outputState.className = "output-state";
  }
  currentView = view;
  $("#hero").hidden = view !== "status" || !configured;
  setupBox.hidden = view !== "status" || configured;
  $("#output").hidden = view !== "output";
  $("#package").hidden = view !== "skills";
  $("#package-result").hidden = view !== "export";
  $(".app").scrollTop = 0;
  if (focus) {
    const target = viewFocusTarget(view);
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
}

function openOutput(): void {
  if (currentView !== "output") outputReturn = currentView;
  showView("output");
}

function presentResult(result: RunResult): void {
  renderOutput(result.output || "（沒有輸出）");
  outputState.textContent = result.code === 0 ? "完成" : "未完成";
  outputState.className = `output-state ${result.code === 0 ? "is-ok" : "is-fail"}`;
  outputBody.scrollTop = 0;
}

function errorResult(error: unknown): RunResult {
  return { code: 1, output: `✗ 操作失敗：${String(error)}` };
}

async function perform<T extends RunResult>(
  label: string,
  task: () => Promise<T>,
  reveal = true,
): Promise<T | RunResult | null> {
  if (running || pendingPushToken || !api()) return null;
  if (reveal) openOutput();
  setBusy(true, label);
  outputTitle.textContent = label;
  outputState.textContent = "執行中…";
  outputState.className = "output-state is-running";
  showPlaceholder(`${label}，請稍候…`);
  try {
    const result = await task();
    presentResult(result);
    return result;
  } catch (error) {
    const result = errorResult(error);
    presentResult(result);
    return result;
  } finally {
    setBusy(false);
  }
}

/** CLI 輸出依行首符號上色:✓ 綠、⚠ 黃、✗ 紅、═══ 標題。 */
function localizeOutputLine(raw: string): string | null {
  const line = raw.trimStart();
  const indent = raw.slice(0, raw.length - line.length);
  const content = line.trimEnd();

  if (content === "Commit and push these changes? [y/N]") return null;
  if (content === "═══ Push local configuration") {
    return "═══ 準備保存這台電腦的設定";
  }
  if (content === "ℹ Configuration changes to commit:") {
    return "ℹ 這次要保存的變更：";
  }
  if (content.startsWith("ℹ Commit message: ")) {
    return `${indent}ℹ 保存紀錄名稱：${content.slice("ℹ Commit message: ".length)}`;
  }
  if (content === "✓ Local configuration committed and pushed") {
    return "✓ 設定已保存並上傳";
  }

  return raw
    .replace(
      /\((\d+) files only in ai-config(?:; repo modified [^)]+)?\)/,
      "（$1 個檔案只在已保存設定）",
    )
    .replace(
      /\((\d+) files only in live; apply removes\)/,
      "（$1 個檔案只在這台電腦；套用時會移除）",
    )
    .replace(
      /(\d+) files? changed, (\d+) insertions?\(\+\), (\d+) deletions?\(-\)/,
      "$1 個檔案有變更，新增 $2 行，移除 $3 行",
    );
}

let lastOutput = "";

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // pywebview 某些平臺不開放 clipboard API,退回隱藏 textarea + execCommand
    const focused = document.activeElement as HTMLElement | null;
    copyFallback.value = text;
    copyFallback.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      focused?.focus();
    }
  }
}

function renderOutput(text: string): void {
  lastOutput = text;
  outputCopy.hidden = text.trim() === "";
  const lines = text.replace(/\n+$/, "").split("\n");
  // 一次組好再插入:上千行的 diff 逐行 append 會反覆重排,
  // 在 WebView2 / WebKit2GTK 上明顯卡頓
  const fragment = document.createDocumentFragment();
  for (const source of lines) {
    const raw = localizeOutputLine(source);
    if (raw === null) continue;
    const span = document.createElement("span");
    const line = raw.trimStart();
    if (line.startsWith("✓")) span.className = "line-ok";
    else if (line.startsWith("⚠")) span.className = "line-warn";
    else if (line.startsWith("✗")) span.className = "line-err";
    else if (line.startsWith("═══")) span.className = "line-head";
    else if (line.startsWith("ℹ")) span.className = "line-dim";
    else if (line.startsWith("+")) span.className = "line-add";
    else if (/^-(?!\s)/.test(line)) span.className = "line-remove";
    else if (/\|\s+\d+/.test(line)) span.className = "line-stat";
    else if (/^\d+ files? changed/.test(line)) span.className = "line-head";
    span.textContent = raw;
    fragment.append(span, document.createTextNode("\n"));
  }
  outputBody.replaceChildren(fragment);
}

outputCopy.addEventListener("click", async () => {
  const ok = await copyText(lastOutput);
  outputCopy.textContent = ok ? "已複製！" : "複製失敗";
  setTimeout(() => {
    outputCopy.textContent = "複製輸出";
  }, 2000);
});

function showPlaceholder(text: string): void {
  lastOutput = "";
  outputCopy.hidden = true;
  const span = document.createElement("span");
  span.className = "line-placeholder";
  span.textContent = text;
  outputBody.replaceChildren(span);
}

function installMessage(zips: string[]): string {
  const paths = zips.map((z) => `- ${z}`).join("\n");
  return [
    "請幫我安裝以下 AI 技能，ZIP 檔在這些路徑：",
    paths,
    "",
    "請使用這個 AI 工具目前支援的技能安裝方式處理。",
    "如果可以直接匯入 ZIP，就直接匯入；否則解壓縮到正確的技能目錄。",
    "完成後請列出已安裝的技能名稱，讓我確認。",
  ].join("\n");
}

function firstErrorLine(output: string): string {
  for (const raw of output.split("\n")) {
    const line = raw.trim();
    if (/^[✗⚠]/.test(line)) {
      return line.replace(/^[✗⚠]\s*/, "");
    }
  }
  return "請查看詳細結果。";
}

function setHero(mark: "ok" | "pending" | "fail" | "none", title: string, sub: string): void {
  heroMark.className = mark === "none" ? "hero-mark" : `hero-mark is-${mark}`;
  heroTitle.textContent = title;
  heroSub.textContent = sub;
}

function invalidateToolStates(label = "需重新檢查"): void {
  for (const row of toolRows) {
    row.className = "tool-row";
    row.querySelector<HTMLElement>(".tool-state")!.textContent = label;
  }
  setHero("none", "尚待確認", "設定可能已改變，請重新檢查工具與本機保存設定是否一致。");
}

function updateStatus(result: RunResult, tool: string): void {
  if (result.code !== 0) {
    invalidateToolStates();
    setHero("fail", "檢查未完成", firstErrorLine(result.output));
    return;
  }
  type ToolState = "ok" | "pending" | "unavailable";
  const states = new Map<string, ToolState>();
  let current = "";
  for (const raw of result.output.split("\n")) {
    const line = raw.trim();
    if (line.startsWith("═══")) {
      current = /^═+\s*Status:\s*(\S+)/.exec(line)?.[1] ?? "";
      if (current) states.set(current, "pending");
    } else if (current && line.includes("No differences found")) {
      states.set(current, "ok");
    } else if (current && /No config in|Tool home directory not found/.test(line)) {
      states.set(current, "unavailable");
    }
  }
  const expected = toolRows.filter(row => tool === "all" || row.dataset.toolRow === tool);
  let pending = 0;
  let unknown = 0;
  for (const row of toolRows) {
    const state = expected.includes(row) ? states.get(row.dataset.toolRow ?? "") : undefined;
    const label = row.querySelector<HTMLElement>(".tool-state")!;
    row.className = "tool-row";
    if (state === "ok") {
      row.classList.add("is-ok");
      label.textContent = "一致";
    } else if (state === "pending") {
      row.classList.add("is-pending");
      label.textContent = "有差異";
      pending += 1;
    } else {
      label.textContent = state === "unavailable" ? "缺少設定／工具" : "未檢查";
      if (expected.includes(row)) unknown += 1;
    }
  }
  const scope = toolLabel(tool);
  if (unknown) {
    setHero("pending", `${scope}尚未全部確認`, "有工具缺少設定或未取得檢查結果，請查看詳細輸出。");
  } else if (pending) {
    setHero("pending", `${pending} 個工具有差異`, "比較的是工具設定與本機保存設定；請查看差異再決定如何同步。");
  } else {
    setHero("ok", `${scope}設定一致`, "與本機保存的設定一致；尚未檢查雲端是否有新版本。");
  }
}

async function runCommand(cmd: AcgCommand): Promise<void> {
  const bridge = api();
  if (!bridge || running || !configured || pendingPushToken || restartRequired) return;
  const tool = selectedTool;
  invalidateToolStates(cmd === "status" ? "檢查中…" : "需重新檢查");
  setHero("none", `${COMMAND_LABELS[cmd]}中…`, `操作範圍：${toolLabel(tool)}`);
  const result = await perform(`${COMMAND_LABELS[cmd]}（${toolLabel(tool)}）`,
    () => bridge.run(cmd, tool), cmd !== "status");
  if (!result) return;
  if (cmd === "status") updateStatus(result, tool);
  else {
    invalidateToolStates();
    if (result.code !== 0) setHero("fail", "操作未完成", firstErrorLine(result.output));
  }
  if (result.code !== 0) openOutput();
}

async function previewPush(): Promise<void> {
  const bridge = api();
  if (!bridge || !configured || restartRequired) return;
  const tool = selectedTool;
  const result = await perform(`上傳前預覽（${toolLabel(tool)}）`, () => bridge.preview_push(tool));
  if (!result || result.code !== 0) return;
  if (!("needs_confirmation" in result) || !result.needs_confirmation) {
    outputState.textContent = "沒有待上傳內容";
    return;
  }
  if (!("token" in result) || typeof result.token !== "string" || !result.token) {
    presentResult({ code: 1, output: "✗ 未取得有效預覽，請返回後重新預覽。" });
    return;
  }
  pendingPushToken = result.token;
  pendingPushTool = tool;
  confirmBox.hidden = false;
  outputState.textContent = "等你確認";
  outputState.className = "output-state is-review";
  syncControls();
  // 焦點先留在可捲動的變更內容，避免直接落在執行上傳的按鈕。
  outputBody.tabIndex = 0;
  outputBody.focus();
}

for (const tab of toolTabs) {
  tab.addEventListener("click", () => {
    if (running || pendingPushToken) return;
    selectedTool = tab.dataset.tool ?? "all";
    for (const candidate of toolTabs) {
      const selected = candidate === tab;
      candidate.classList.toggle("is-selected", selected);
      candidate.setAttribute("aria-checked", String(selected));
      candidate.tabIndex = selected ? 0 : -1;
    }
  });
  tab.addEventListener("keydown", (event) => {
    let index: number;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      index = (toolTabs.indexOf(tab) + 1) % toolTabs.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      index = (toolTabs.indexOf(tab) - 1 + toolTabs.length) % toolTabs.length;
    } else if (event.key === "Home") {
      index = 0;
    } else if (event.key === "End") {
      index = toolTabs.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    toolTabs[index].click();
    toolTabs[index].focus();
  });
}
for (const button of document.querySelectorAll<HTMLButtonElement>("[data-cmd]")) {
  button.addEventListener("click", () => {
    const cmd = button.dataset.cmd as AcgCommand;
    void (cmd === "push" ? previewPush() : runCommand(cmd));
  });
}
confirmYes.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || !pendingPushToken || running || currentView !== "output") return;
  const token = pendingPushToken;
  const tool = pendingPushTool;
  cancelPush();
  invalidateToolStates();
  await perform(`上傳變更（${toolLabel(tool)}）`, () => bridge.confirm_push(tool, token));
});
$("#confirm-no").addEventListener("click", () => {
  cancelPush();
  outputState.textContent = "已取消確認，尚未上傳";
  outputState.className = "output-state";
  $("#output-back").focus();
});

function matchesSkillFilter(skill: SkillEntry, filter: string): boolean {
  if (filter === "shared") return skill.shared;
  if (filter === "unshared") return !skill.shared;
  return true;
}

function visibleSkills(): SkillEntry[] {
  const query = skillSearch.value.trim().toLocaleLowerCase();
  return skills.filter(
    (skill) =>
      skill.name.toLocaleLowerCase().includes(query) &&
      matchesSkillFilter(skill, skillFilter.value),
  );
}

function renderSkills(): void {
  skillList.replaceChildren();
  const visible = visibleSkills();
  if (!visible.length) {
    const message = document.createElement("p");
    message.className = "package-hint";
    message.textContent = skills.length ? "沒有符合條件的技能，請調整搜尋或篩選。" : "目前沒有可用的技能。";
    skillList.append(message);
  }
  for (const skill of visible) {
    const label = document.createElement("label");
    label.className = "skill-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = skill.name;
    checkbox.checked = selectedSkills.has(skill.name);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedSkills.add(skill.name);
      else selectedSkills.delete(skill.name);
      syncControls();
    });
    const name = document.createElement("span");
    name.className = "skill-name";
    name.textContent = skill.name;
    label.append(checkbox, name);
    if (skill.shared) {
      const tag = document.createElement("span");
      tag.className = "skill-tag";
      tag.textContent = "已分享";
      label.append(tag);
    }
    skillList.append(label);
  }
  syncControls();
}

async function loadSkills(): Promise<void> {
  const bridge = api();
  if (!bridge || skillsLoading) return;
  skillsLoading = true;
  skillRetry.hidden = true;
  skillList.setAttribute("aria-busy", "true");
  skillList.textContent = "讀取技能中…";
  syncControls();
  try {
    const result = await bridge.list_skills();
    skills = result.skills;
    for (const name of selectedSkills) {
      if (!skills.some(skill => skill.name === name)) selectedSkills.delete(name);
    }
    renderSkills();
  } catch (error) {
    skills = [];
    selectedSkills.clear();
    skillList.textContent = `無法讀取技能：${String(error)}`;
    skillRetry.hidden = false;
  } finally {
    skillsLoading = false;
    skillList.setAttribute("aria-busy", "false");
    syncControls();
  }
}
skillSearch.addEventListener("input", renderSkills);
skillFilter.addEventListener("change", renderSkills);
$("#skill-all").addEventListener("click", () => {
  for (const skill of visibleSkills()) selectedSkills.add(skill.name);
  renderSkills();
});
$("#skill-none").addEventListener("click", () => {
  selectedSkills.clear();
  renderSkills();
});
skillRetry.addEventListener("click", () => { void loadSkills(); });

const SKILL_ACTION_LABELS = {
  share: "分享技能",
  unshare: "取消分享",
  package: "匯出安裝檔",
} as const;

async function skillAction(action: "share" | "unshare" | "package"): Promise<void> {
  const bridge = api();
  if (!bridge || running || pendingPushToken || !selectedSkills.size) return;
  const names = [...selectedSkills];
  const label = SKILL_ACTION_LABELS[action];
  packageMessage.value = "";
  $("#package-result").hidden = true;
  feedback(skillResult, "");
  const result = await perform(label, async () => {
    let res: RunResult;
    if (action === "package") {
      res = await bridge.package_skills(names);
    } else if (action === "share") {
      res = await bridge.share_skills(names);
    } else {
      res = await bridge.unshare_skills(names);
    }
    if (action !== "package") {
      invalidateToolStates();
      await loadSkills();
    }
    return res;
  }, false);
  if (!result) return;
  feedback(skillResult, result.code === 0 ? `${label}完成。` : firstErrorLine(result.output), result.code !== 0);
  if (action === "package" && "zips" in result && Array.isArray(result.zips) && result.zips.length) {
    packageMessage.value = installMessage(result.zips);
    const statusText = result.code === 0
      ? `已準備 ${result.zips.length} 個安裝檔。`
      : `僅完成 ${result.zips.length} / ${names.length} 項。${firstErrorLine(result.output)}`;
    feedback($("#export-status"), statusText, result.code !== 0);
    showView("export");
  }
}
$("#skill-share").addEventListener("click", () => { void skillAction("share"); });
$("#skill-unshare").addEventListener("click", () => { void skillAction("unshare"); });
$("#skill-package").addEventListener("click", () => { void skillAction("package"); });
packageCopy.addEventListener("click", async () => {
  packageMessage.select();
  const copied = await copyText(packageMessage.value);
  if (!copied) { packageMessage.focus(); packageMessage.select(); }
  packageCopy.textContent = copied ? "已複製" : "請按複製快捷鍵";
  setTimeout(() => { packageCopy.textContent = "複製說明"; }, 2000);
});

updateBtn.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running || pendingPushToken || updateInstalled) return;
  if (pendingUpdate) {
    const result = await perform(`更新到 v${pendingUpdate}`, () => bridge.run_update());
    if (result?.code === 0) {
      updateInstalled = true;
      pendingUpdate = null;
      updateBtn.textContent = "已更新，重開視窗生效";
    }
  } else {
    updateBtn.textContent = "檢查中…";
    const result = await perform("檢查更新", () => bridge.check_update(), false);
    updateBtn.textContent = "檢查更新";
    if (result?.code === 0 && "latest" in result && typeof result.latest === "string") {
      if ("up_to_date" in result && result.up_to_date) {
        feedback(appNotice, "目前已是最新版本。");
      } else {
        pendingUpdate = result.latest;
        updateBtn.textContent = `更新到 v${pendingUpdate}`;
        feedback(appNotice, `可更新到 v${pendingUpdate}，按上方更新按鈕開始安裝。`);
      }
    } else if (result) openOutput();
  }
  syncControls();
});
$("#config-info").addEventListener("click", () => {
  const bridge = api();
  if (bridge) void perform("連線資訊", () => bridge.config_info());
});

function field(panel: HTMLElement, name: string): HTMLInputElement {
  const input = panel.querySelector<HTMLInputElement>(`[data-field="${name}"]`);
  if (!input) throw new Error(`missing field: ${name}`);
  return input;
}

function bindSpace(panel: HTMLElement): void {
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

async function submitSetup(provider: string, panel: HTMLElement, switching = false): Promise<void> {
  const bridge = api();
  if (!bridge || running || pendingPushToken) return;
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
  const folder = provider === "gdrive" ? field(panel, "gdrive-folder").value : "";
  const space = panel.querySelector<HTMLInputElement>('[data-field="gdrive-space"]:checked')?.value ?? "visible";
  if (switching) showSettings(false);
  const task = () =>
    provider === "git"
      ? bridge.setup_repo(repoUrl, dataDir)
      : bridge.setup_gdrive(dataDir, folder, space);
  const result = await perform(switching ? "切換同步方式" : "首次設定", task);
  if (result?.code === 0) {
    // 後端工具路徑於啟動時載入；完成 setup 後不可使用舊程序繼續同步。
    restartRequired = true;
    feedback(appNotice, "設定完成。請關閉並重新開啟視窗，載入新的連線設定。");
    syncControls();
  }
}
$("#setup-go").addEventListener("click", () => { void submitSetup("git", setupGitPanel); });
$("#setup-gdrive-go").addEventListener("click", () => { void submitSetup("gdrive", setupGdrivePanel); });

async function loadInfo(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  try {
    const info = await bridge.get_info();
    connected = true;
    configured = info.configured;
    versionEl.textContent = info.build_commit
      ? `v${info.version} · ${info.build_commit.slice(0, 8)}`
      : `v${info.version}`;
    versionEl.title = info.build_commit || "";
    providerEl.textContent = configured ? providerLabel(info.provider) : "尚未設定同步方式";
    providerEl.dataset.provider = configured ? info.provider : "none";
    repoEl.textContent = `本機設定位置：${info.repo}`;
    repoEl.title = info.repo;
    field(setupGitPanel, "data-dir").value = info.repo;
    field(setupGdrivePanel, "data-dir").value = info.repo;
    feedback(appNotice, info.config_error ? `設定檔有問題：${info.config_error}` : "", Boolean(info.config_error));
    $("#info-retry").hidden = true;
    showView(currentView, false);
  } catch (error) {
    connected = false;
    feedback(appNotice, `無法讀取連線資訊：${String(error)}`, true);
    $("#info-retry").hidden = false;
  } finally {
    syncControls();
  }
}
$("#info-retry").addEventListener("click", () => { void boot(); });

function providerLabel(provider: string): string {
  return provider === "gdrive" ? "Google Drive" : "私人 Git 儲存庫";
}

// ── GitHub 上傳權限 ─────────────────────

let githubPollTimer: number | null = null;

function stopGithubPolling(): void {
  if (githubPollTimer !== null) {
    window.clearTimeout(githubPollTimer);
    githubPollTimer = null;
  }
}

function resetGithubCode(): void {
  stopGithubPolling();
  githubCode.hidden = true;
  githubCodeValue.textContent = "————";
  githubCodeHint.textContent = "";
  githubLoginBtn.disabled = false;
  githubLoginBtn.textContent = "用瀏覽器登入 GitHub";
}

async function loadGithubAccess(): Promise<void> {
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
  githubActions.hidden = !access.installed;
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

githubCodeCopy.addEventListener("click", async () => {
  const ok = await copyText(githubCodeValue.textContent ?? "");
  githubCodeCopy.textContent = ok ? "已複製！" : "請手動複製";
  window.setTimeout(() => {
    githubCodeCopy.textContent = "複製";
  }, 2000);
});

async function loadSettings(): Promise<void> {
  const bridge = api();
  if (!bridge || settingsLoading) return;
  settingsLoading = true;
  settingsInfo = null;
  settingsSwitch.hidden = true;
  settingsRetry.hidden = true;
  feedback(settingsFeedback, "讀取設定中…");
  syncControls();
  try {
    const info = await bridge.settings_info();
    settingsInfo = info;
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
    settingsLoading = false;
    syncControls();
  }
}

function showSettings(show: boolean): void {
  settingsBox.hidden = !show;
  // 關閉時停止輪詢並清掉畫面:否則離開後還在打 GitHub 的 API,
  // 而且下次打開會看到一組早就失效的舊驗證碼
  if (!show) resetGithubCode();
  for (const child of Array.from($(".app").children)) {
    if (child instanceof HTMLElement && child !== settingsBox) child.inert = show;
  }
  if (show) {
    settingsOpener = document.activeElement as HTMLElement | null;
    settingsClose.focus();
    void loadSettings();
  } else {
    settingsOpener?.focus();
    settingsOpener = null;
  }
}
$("#settings-open").addEventListener("click", () => { showSettings(true); });
settingsClose.addEventListener("click", () => { showSettings(false); });
settingsRetry.addEventListener("click", () => { void loadSettings(); });
settingsBox.addEventListener("click", event => {
  if (event.target === settingsBox) showSettings(false);
});
document.addEventListener("keydown", event => {
  if (settingsBox.hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    showSettings(false);
  } else if (event.key === "Tab") {
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
  if (!bridge || settingsInfo?.provider !== "gdrive") return;
  feedback(settingsFeedback, "請在瀏覽器完成 Google 登入。儲存位置保持不變。");
  const result = await perform("重新登入 Google", () => bridge.relogin_gdrive(), false);
  if (!result) return;
  if (result.code === 0) await loadSettings();
  feedback(settingsFeedback, result.code === 0 ? "已重新登入，儲存位置保持不變。" : firstErrorLine(result.output), result.code !== 0);
  if (settingsBox.hidden) openOutput();
});

function offerProviderSwitch(provider: string): void {
  const info = settingsInfo;
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
    if (radio.value === settingsInfo?.provider) settingsSwitch.hidden = true;
    else offerProviderSwitch(radio.value);
  });
}

$("#package-open").addEventListener("click", () => { showView("skills"); });
$("#package-back").addEventListener("click", () => { showView("status"); });
$("#output-toggle").addEventListener("click", openOutput);
$("#skill-output").addEventListener("click", openOutput);
$("#output-back").addEventListener("click", () => { showView(outputReturn); });
$("#export-back").addEventListener("click", () => { showView("skills"); });

async function boot(): Promise<void> {
  syncControls();
  await loadInfo();
  if (connected && configured) await loadSkills();
}
if (window.pywebview) void boot();
else {
  syncControls();
  window.addEventListener("pywebviewready", () => { void boot(); }, { once: true });
  repoEl.textContent = "等待後端連線…";
  feedback(appNotice, "正在連接應用程式；請透過 acg gui 啟動此介面。");
}
