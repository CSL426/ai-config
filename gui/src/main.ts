import "./style.css";

import type { AcgApi, AcgCommand, SkillEntry } from "./bridge";

const COMMAND_LABELS: Record<AcgCommand, string> = {
  status: "檢查狀態",
  apply: "套用設定",
  pull: "下載更新",
  push: "上傳變更",
};

const $ = <T extends HTMLElement>(sel: string): T => {
  const el = document.querySelector<T>(sel);
  if (!el) throw new Error(`missing element: ${sel}`);
  return el;
};

const versionEl = $("#version");
const repoEl = $("#repo");
const providerEl = $("#provider");
const configInfoBtn = $<HTMLButtonElement>("#config-info");
const toolTabs = Array.from(
  document.querySelectorAll<HTMLButtonElement>("#tool-tabs .scope-tab"),
);
const actionButtons = Array.from(
  document.querySelectorAll<HTMLButtonElement>(".action"),
);
const confirmBox = $("#confirm");
const confirmYes = $<HTMLButtonElement>("#confirm-yes");
const confirmNo = $<HTMLButtonElement>("#confirm-no");
const outputTitle = $("#output-title");
const outputState = $("#output-state");
const outputBody = $("#output-body");

let selectedTool = "all";
let running = false;
let configured = false;
let awaitingPushConfirmation = false;
let pendingPushToken: string | null = null;
let pendingPushTool = "all";

function api(): AcgApi | null {
  return window.pywebview?.api ?? null;
}

function setBusy(busy: boolean, cmd?: AcgCommand): void {
  running = busy;
  configInfoBtn.disabled = busy || awaitingPushConfirmation;
  for (const btn of actionButtons) {
    btn.disabled = busy || !configured || awaitingPushConfirmation;
    btn.classList.toggle("is-running", busy && btn.dataset.cmd === cmd);
  }
  for (const tab of toolTabs) {
    tab.disabled = busy || !configured || awaitingPushConfirmation;
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

function renderOutput(text: string): void {
  outputBody.replaceChildren();
  const lines = text.replace(/\n+$/, "").split("\n");
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
    else if (line.startsWith("-")) span.className = "line-remove";
    else if (/\|\s+\d+/.test(line)) span.className = "line-stat";
    else if (/^\d+ files? changed/.test(line)) span.className = "line-head";
    span.textContent = raw;
    outputBody.append(span, document.createTextNode("\n"));
  }
}

function showPlaceholder(text: string): void {
  const span = document.createElement("span");
  span.className = "line-placeholder";
  span.textContent = text;
  outputBody.replaceChildren(span);
}

async function previewPush(): Promise<void> {
  const bridge = api();
  if (!bridge || running) return;

  awaitingPushConfirmation = false;
  pendingPushToken = null;
  confirmBox.hidden = true;
  setBusy(true, "push");
  outputTitle.textContent = `上傳前預覽(${toolLabel()})`;
  outputState.textContent = "整理中…";
  outputState.className = "output-state is-running";
  showPlaceholder("正在整理變更摘要,不會上傳任何內容…");

  try {
    const result = await bridge.preview_push(selectedTool);
    renderOutput(result.output || "(沒有輸出)");
    if (result.code !== 0) {
      outputState.textContent = "有問題";
      outputState.className = "output-state is-fail";
      return;
    }
    if (!result.needs_confirmation) {
      outputState.textContent = "沒有待上傳內容";
      outputState.className = "output-state is-ok";
      return;
    }

    pendingPushToken = result.token;
    pendingPushTool = selectedTool;
    awaitingPushConfirmation = true;
    outputState.textContent = "等你確認";
    outputState.className = "output-state is-review";
    confirmBox.hidden = false;
    confirmYes.focus();
  } catch (err) {
    showPlaceholder(`無法產生預覽:${String(err)}`);
    outputState.textContent = "有問題";
    outputState.className = "output-state is-fail";
  } finally {
    setBusy(false);
    outputBody.scrollTop = 0;
  }
}

async function runCommand(cmd: AcgCommand): Promise<void> {
  const bridge = api();
  if (!bridge) {
    showPlaceholder("尚未連上後端 — 請透過「acg gui」啟動,而不是直接開啟網頁。");
    return;
  }
  if (running) return;

  setBusy(true, cmd);
  outputTitle.textContent = `${COMMAND_LABELS[cmd]}(${toolLabel()})`;
  outputState.textContent = "執行中…";
  outputState.className = "output-state is-running";
  showPlaceholder("執行中,請稍候…");

  try {
    const result = await bridge.run(cmd, selectedTool);
    renderOutput(result.output || "(沒有輸出)");
    if (result.code === 0) {
      outputState.textContent = "完成";
      outputState.className = "output-state is-ok";
    } else {
      outputState.textContent = "有問題";
      outputState.className = "output-state is-fail";
    }
  } catch (err) {
    showPlaceholder(`執行失敗:${String(err)}`);
    outputState.textContent = "有問題";
    outputState.className = "output-state is-fail";
  } finally {
    setBusy(false);
    outputBody.scrollTop = 0;
  }
}

function toolLabel(): string {
  const tab = toolTabs.find((t) => t.dataset.tool === selectedTool);
  return tab?.textContent?.trim() ?? selectedTool;
}

for (const tab of toolTabs) {
  tab.addEventListener("click", () => {
    if (running) return;
    selectedTool = tab.dataset.tool ?? "all";
    for (const t of toolTabs) {
      const on = t === tab;
      t.classList.toggle("is-selected", on);
      t.setAttribute("aria-checked", on ? "true" : "false");
    }
  });
}

for (const btn of actionButtons) {
  btn.addEventListener("click", () => {
    const cmd = btn.dataset.cmd as AcgCommand | undefined;
    if (!cmd || running) return;
    if (cmd === "push") {
      void previewPush();
      return;
    }
    confirmBox.hidden = true;
    void runCommand(cmd);
  });
}

confirmYes.addEventListener("click", async () => {
  const bridge = api();
  const token = pendingPushToken;
  const tool = pendingPushTool;
  if (!bridge || !token || running) return;

  awaitingPushConfirmation = false;
  pendingPushToken = null;
  confirmBox.hidden = true;
  setBusy(true, "push");
  outputTitle.textContent = `上傳變更(${toolLabel()})`;
  outputState.textContent = "再次核對中…";
  outputState.className = "output-state is-running";
  showPlaceholder("正在確認內容仍和預覽相同,相同才會上傳…");
  try {
    const result = await bridge.confirm_push(tool, token);
    renderOutput(result.output || "(沒有輸出)");
    outputState.textContent = result.code === 0 ? "完成" : "有問題";
    outputState.className =
      result.code === 0 ? "output-state is-ok" : "output-state is-fail";
  } catch (err) {
    showPlaceholder(`上傳失敗:${String(err)}`);
    outputState.textContent = "有問題";
    outputState.className = "output-state is-fail";
  } finally {
    setBusy(false);
    outputBody.scrollTop = 0;
  }
});
confirmNo.addEventListener("click", () => {
  awaitingPushConfirmation = false;
  pendingPushToken = null;
  confirmBox.hidden = true;
  outputState.textContent = "未上傳";
  outputState.className = "output-state";
  setBusy(false);
});

// ── 技能打包 ───────────────────────────

const skillList = $("#skill-list");
const skillAll = $<HTMLButtonElement>("#skill-all");
const skillNone = $<HTMLButtonElement>("#skill-none");
const skillShare = $<HTMLButtonElement>("#skill-share");
const skillUnshare = $<HTMLButtonElement>("#skill-unshare");
const skillPackage = $<HTMLButtonElement>("#skill-package");
const updateBtn = $<HTMLButtonElement>("#update-check");
const packageResult = $("#package-result");
const packageMessage = $<HTMLTextAreaElement>("#package-message");
const packageCopy = $<HTMLButtonElement>("#package-copy");

function skillCheckboxes(): HTMLInputElement[] {
  return Array.from(
    skillList.querySelectorAll<HTMLInputElement>("input[type=checkbox]"),
  );
}

function renderSkills(skills: SkillEntry[]): void {
  skillList.replaceChildren();
  if (skills.length === 0) {
    const p = document.createElement("p");
    p.className = "package-hint";
    p.textContent = "找不到可用的技能。";
    skillList.append(p);
    return;
  }
  for (const skill of skills) {
    const label = document.createElement("label");
    label.className = "skill-item";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = skill.name;
    const text = document.createElement("span");
    text.textContent = skill.name;
    label.append(box, text);
    if (skill.shared) {
      const tag = document.createElement("span");
      tag.className = "skill-tag";
      tag.textContent = "已共享";
      label.append(tag);
    }
    skillList.append(label);
  }
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

skillAll.addEventListener("click", () => {
  for (const box of skillCheckboxes()) box.checked = true;
});
skillNone.addEventListener("click", () => {
  for (const box of skillCheckboxes()) box.checked = false;
});

function selectedSkills(): string[] {
  return skillCheckboxes()
    .filter((box) => box.checked)
    .map((box) => box.value);
}

skillShare.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;
  outputTitle.textContent = "分享技能";
  const result = await bridge.share_skills(selectedSkills());
  renderOutput(result.output);
  outputState.textContent = result.code === 0 ? "完成" : "有問題";
  outputState.className =
    result.code === 0 ? "output-state is-ok" : "output-state is-fail";
  void loadSkills();
});

skillUnshare.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;
  outputTitle.textContent = "取消分享";
  const result = await bridge.unshare_skills(selectedSkills());
  renderOutput(result.output);
  outputState.textContent = result.code === 0 ? "完成" : "有問題";
  outputState.className =
    result.code === 0 ? "output-state is-ok" : "output-state is-fail";
  void loadSkills();
});

skillPackage.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;
  outputTitle.textContent = "打包技能";
  const result = await bridge.package_skills(selectedSkills());
  renderOutput(result.output);
  outputState.textContent = result.code === 0 ? "完成" : "有問題";
  outputState.className =
    result.code === 0 ? "output-state is-ok" : "output-state is-fail";
  if (result.zips.length > 0) {
    packageMessage.value = installMessage(result.zips);
    packageResult.hidden = false;
  }
});

packageCopy.addEventListener("click", async () => {
  packageMessage.select();
  try {
    await navigator.clipboard.writeText(packageMessage.value);
    packageCopy.textContent = "已複製!";
  } catch {
    // pywebview 某些平臺不開放 clipboard API;退回選取讓使用者 Ctrl+C
    packageCopy.textContent = "請按 Ctrl+C 複製";
  }
  setTimeout(() => {
    packageCopy.textContent = "複製說明";
  }, 2000);
});

// ── 版本更新 ───────────────────────────

let pendingUpdate: string | null = null;

updateBtn.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;

  if (pendingUpdate) {
    setBusy(true);
    outputTitle.textContent = `更新到 v${pendingUpdate}`;
    outputState.textContent = "執行中…";
    outputState.className = "output-state is-running";
    showPlaceholder("下載並安裝新版本,請稍候…");
    try {
      const result = await bridge.run_update();
      renderOutput(result.output || "(沒有輸出)");
      if (result.code === 0) {
        outputState.textContent = "完成";
        outputState.className = "output-state is-ok";
        updateBtn.textContent = "已更新,重開視窗生效";
        updateBtn.disabled = true;
        pendingUpdate = null;
      } else {
        outputState.textContent = "有問題";
        outputState.className = "output-state is-fail";
      }
    } finally {
      setBusy(false);
    }
    return;
  }

  updateBtn.disabled = true;
  updateBtn.textContent = "檢查中…";
  const check = await bridge.check_update();
  updateBtn.disabled = false;
  if (check.code !== 0) {
    renderOutput(check.output);
    outputTitle.textContent = "檢查更新";
    outputState.textContent = "有問題";
    outputState.className = "output-state is-fail";
    updateBtn.textContent = "檢查更新";
  } else if (check.up_to_date) {
    updateBtn.textContent = "已是最新版";
    setTimeout(() => {
      updateBtn.textContent = "檢查更新";
    }, 3000);
  } else {
    pendingUpdate = check.latest;
    updateBtn.textContent = `更新到 v${check.latest}`;
  }
});

async function loadSkills(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  try {
    const result = await bridge.list_skills();
    renderSkills(result.skills);
  } catch {
    renderSkills([]);
  }
}

configInfoBtn.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;

  setBusy(true);
  outputTitle.textContent = "連線資訊";
  outputState.textContent = "讀取中…";
  outputState.className = "output-state is-running";
  showPlaceholder("正在讀取同步方式與登入狀態…");
  try {
    const result = await bridge.config_info();
    renderOutput(result.output || "(沒有輸出)");
    outputState.textContent = result.code === 0 ? "唯讀" : "有問題";
    outputState.className =
      result.code === 0 ? "output-state is-ok" : "output-state is-fail";
  } catch (err) {
    showPlaceholder(`無法讀取連線資訊：${String(err)}`);
    outputState.textContent = "有問題";
    outputState.className = "output-state is-fail";
  } finally {
    setBusy(false);
    outputBody.scrollTop = 0;
  }
});

// ── 首次設定 ───────────────────────────

const setupBox = $("#setup");
const setupUrl = $<HTMLInputElement>("#setup-url");
const setupDir = $<HTMLInputElement>("#setup-dir");
const setupGo = $<HTMLButtonElement>("#setup-go");
const setupGdriveDir = $<HTMLInputElement>("#setup-gdrive-dir");
const setupGdriveFolder = $<HTMLInputElement>("#setup-gdrive-folder");
const setupGdriveFolderField = $("#setup-gdrive-folder-field");
const gdriveSpaceRadios = Array.from(
  document.querySelectorAll<HTMLInputElement>("input[name=gdrive-space]"),
);

function selectedGdriveSpace(): string {
  return gdriveSpaceRadios.find((radio) => radio.checked)?.value ?? "visible";
}

for (const radio of gdriveSpaceRadios) {
  radio.addEventListener("change", () => {
    // 隱藏空間沒有資料夾路徑可填
    setupGdriveFolderField.hidden = selectedGdriveSpace() === "hidden";
  });
}
const setupGdriveGo = $<HTMLButtonElement>("#setup-gdrive-go");
const setupGitPanel = $("#setup-git-panel");
const setupGdrivePanel = $("#setup-gdrive-panel");
const providerRadios = Array.from(
  document.querySelectorAll<HTMLInputElement>("input[name=setup-provider]"),
);

for (const radio of providerRadios) {
  radio.addEventListener("change", () => {
    const isGdrive = radio.value === "gdrive" && radio.checked;
    setupGitPanel.hidden = isGdrive;
    setupGdrivePanel.hidden = !isGdrive;
    if (isGdrive) {
      setupGdriveDir.value = setupDir.value;
    }
  });
}

function setConfigured(value: boolean): void {
  configured = value;
  setupBox.hidden = value;
  setBusy(running);
  skillShare.disabled = !value;
  skillUnshare.disabled = !value;
  skillPackage.disabled = !value;
}

setupGo.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;
  setupGo.disabled = true;
  setupGo.textContent = "設定中…";
  outputTitle.textContent = "首次設定";
  outputState.textContent = "執行中…";
  outputState.className = "output-state is-running";
  showPlaceholder("正在下載儲存庫並驗證存取權,請稍候…");
  try {
    const result = await bridge.setup_repo(setupUrl.value, setupDir.value);
    renderOutput(result.output || "(沒有輸出)");
    if (result.code === 0) {
      outputState.textContent = "完成";
      outputState.className = "output-state is-ok";
      setupBox.replaceChildren();
      const done = document.createElement("p");
      done.className = "package-hint";
      done.textContent =
        "✓ 設定完成!請關閉並重新開啟這個視窗,就能開始同步。";
      setupBox.append(done);
    } else {
      outputState.textContent = "有問題";
      outputState.className = "output-state is-fail";
    }
  } finally {
    setupGo.disabled = false;
    setupGo.textContent = "連線並完成設定";
  }
});

setupGdriveGo.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;
  setupGdriveGo.disabled = true;
  setupGdriveGo.textContent = "驗證中…";
  outputTitle.textContent = "首次設定 (Google Drive)";
  outputState.textContent = "執行中…";
  outputState.className = "output-state is-running";
  showPlaceholder("已開啟瀏覽器,請完成登入…");
  try {
    const result = await bridge.setup_gdrive(
      setupGdriveDir.value,
      setupGdriveFolder.value,
      selectedGdriveSpace(),
    );
    renderOutput(result.output || "(沒有輸出)");
    if (result.code === 0) {
      outputState.textContent = "完成";
      outputState.className = "output-state is-ok";
      setupBox.replaceChildren();
      const done = document.createElement("p");
      done.className = "package-hint";
      done.textContent =
        "✓ Google Drive 設定完成!請關閉並重新開啟這個視窗,就能開始同步。";
      setupBox.append(done);
    } else {
      outputState.textContent = "有問題";
      outputState.className = "output-state is-fail";
    }
  } finally {
    setupGdriveGo.disabled = false;
    setupGdriveGo.textContent = "用 Google 帳號登入";
  }
});

async function loadInfo(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  try {
    const info = await bridge.get_info();
    versionEl.textContent = `v${info.version}`;
    providerEl.textContent =
      info.provider === "gdrive" ? "Google Drive" : "私人 Git 儲存庫";
    providerEl.dataset.provider = info.provider;
    repoEl.textContent = `本機設定位置：${info.repo}`;
    repoEl.title = info.repo;
    setConfigured(info.configured);
    if (!info.configured) {
      setupDir.value = info.repo;
      setupGdriveDir.value = info.repo;
      providerEl.textContent = "尚未選擇同步方式";
      providerEl.dataset.provider = "none";
      repoEl.textContent = `新設定預設位置：${info.repo}`;
      if (info.config_error) {
        showPlaceholder(`設定檔有問題:${info.config_error}`);
      }
    }
  } catch {
    repoEl.textContent = "無法讀取儲存庫資訊";
  }
}

function boot(): void {
  void loadInfo();
  void loadSkills();
}

if (window.pywebview) {
  boot();
} else {
  window.addEventListener("pywebviewready", boot, { once: true });
  repoEl.textContent = "等待後端連線…";
}
