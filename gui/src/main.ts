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

function api(): AcgApi | null {
  return window.pywebview?.api ?? null;
}

function setBusy(busy: boolean, cmd?: AcgCommand): void {
  running = busy;
  configInfoBtn.disabled = busy;
  for (const btn of actionButtons) {
    btn.disabled = busy || !configured;
    btn.classList.toggle("is-running", busy && btn.dataset.cmd === cmd);
  }
  for (const tab of toolTabs) tab.disabled = busy || !configured;
}

/** CLI 輸出依行首符號上色:✓ 綠、⚠ 黃、✗ 紅、═══ 標題。 */
function renderOutput(text: string): void {
  outputBody.replaceChildren();
  const lines = text.replace(/\n+$/, "").split("\n");
  for (const raw of lines) {
    const span = document.createElement("span");
    const line = raw.trimStart();
    if (line.startsWith("✓")) span.className = "line-ok";
    else if (line.startsWith("⚠")) span.className = "line-warn";
    else if (line.startsWith("✗")) span.className = "line-err";
    else if (line.startsWith("═══")) span.className = "line-head";
    else if (line.startsWith("ℹ")) span.className = "line-dim";
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
      confirmBox.hidden = false;
      confirmYes.focus();
      return;
    }
    confirmBox.hidden = true;
    void runCommand(cmd);
  });
}

confirmYes.addEventListener("click", () => {
  confirmBox.hidden = true;
  void runCommand("push");
});
confirmNo.addEventListener("click", () => {
  confirmBox.hidden = true;
});

// ── 技能打包 ───────────────────────────

const skillList = $("#skill-list");
const skillAll = $<HTMLButtonElement>("#skill-all");
const skillNone = $<HTMLButtonElement>("#skill-none");
const skillShare = $<HTMLButtonElement>("#skill-share");
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
    const result = await bridge.setup_gdrive(setupGdriveDir.value);
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
      repoEl.textContent = "完成下方設定後即可開始同步";
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
