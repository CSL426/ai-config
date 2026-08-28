import "./style.css";

import type { AcgApi, AcgCommand } from "./bridge";

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

function api(): AcgApi | null {
  return window.pywebview?.api ?? null;
}

function setBusy(busy: boolean, cmd?: AcgCommand): void {
  running = busy;
  for (const btn of actionButtons) {
    btn.disabled = busy;
    btn.classList.toggle("is-running", busy && btn.dataset.cmd === cmd);
  }
  for (const tab of toolTabs) tab.disabled = busy;
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
const skillPackage = $<HTMLButtonElement>("#skill-package");
const packageResult = $("#package-result");
const packageMessage = $<HTMLTextAreaElement>("#package-message");
const packageCopy = $<HTMLButtonElement>("#package-copy");

function skillCheckboxes(): HTMLInputElement[] {
  return Array.from(
    skillList.querySelectorAll<HTMLInputElement>("input[type=checkbox]"),
  );
}

function renderSkills(skills: string[]): void {
  skillList.replaceChildren();
  if (skills.length === 0) {
    const p = document.createElement("p");
    p.className = "package-hint";
    p.textContent = "儲存庫裡沒有可打包的共用技能。";
    skillList.append(p);
    return;
  }
  for (const name of skills) {
    const label = document.createElement("label");
    label.className = "skill-item";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = name;
    const text = document.createElement("span");
    text.textContent = name;
    label.append(box, text);
    skillList.append(label);
  }
}

function installMessage(zips: string[]): string {
  const paths = zips.map((z) => `- ${z}`).join("\n");
  return [
    "請幫我安裝以下 AI 技能(skill),ZIP 檔在這些路徑:",
    paths,
    "",
    "請解壓縮每個 ZIP,把裡面的技能資料夾放進你的技能目錄",
    "(Claude 是 ~/.claude/skills/,Codex 是 ~/.codex/skills/),",
    "完成後列出已安裝的技能名稱讓我確認。",
  ].join("\n");
}

skillAll.addEventListener("click", () => {
  for (const box of skillCheckboxes()) box.checked = true;
});
skillNone.addEventListener("click", () => {
  for (const box of skillCheckboxes()) box.checked = false;
});

skillPackage.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || running) return;
  const selected = skillCheckboxes()
    .filter((box) => box.checked)
    .map((box) => box.value);
  outputTitle.textContent = "打包技能";
  const result = await bridge.package_skills(selected);
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

async function loadInfo(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  try {
    const info = await bridge.get_info();
    versionEl.textContent = `v${info.version}`;
    repoEl.textContent = `資料儲存庫:${info.repo}`;
    repoEl.title = info.repo;
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
