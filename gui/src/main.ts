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

if (window.pywebview) {
  void loadInfo();
} else {
  window.addEventListener("pywebviewready", () => void loadInfo(), {
    once: true,
  });
  repoEl.textContent = "等待後端連線…";
}
