/** The shell every feature sits in: which view is showing, the output
 * panel, the confirm/preview handshake, and the control gate.
 *
 * A feature imports from here; this file imports from no feature. That is
 * the whole rule — it is what stops the graph from closing on itself.
 */

import { closeActiveSelect } from "./select";
import { state, type MainView, type PendingPreview } from "./state";
import {
  $, confirmBox, confirmYes, copyFallback,
  heroTitle, operationStatus, outputBody, outputState,
  outputCopy, outputTitle, settingsBox, settingsRetry, setupBox,
  toolTabs, updateBtn,
} from "./dom";
import type { AcgApi, RunResult } from "./bridge";

export function api(): AcgApi | null {
  return window.pywebview?.api ?? null;
}

export function toolLabel(tool: string = state.selectedTool): string {
  return toolTabs.find(tab => tab.dataset.tool === tool)?.textContent?.trim() ?? tool;
}

export function feedback(element: HTMLElement, text: string, failed = false): void {
  element.textContent = text;
  element.hidden = !text;
  element.classList.toggle("is-fail", failed);
}

/** What every area needs to decide whether its controls are usable. */
export type ControlGate = {
  blocked: boolean;
  configured: boolean;
  restartRequired: boolean;
  managementBlocked: boolean;
};

type Sync = (gate: ControlGate) => void;
const syncHandlers: Sync[] = [];

/** Register an area's own control rules, to run on every syncControls().
 *
 * One function that knew every area's ids is what kept those areas from
 * moving into files of their own: it imported from all of them, so any
 * area importing it back closed a cycle.
 */
export function onSync(handler: Sync): void {
  syncHandlers.push(handler);
}

export function syncControls(): void {
  const blocked = state.running || !state.connected || state.pendingPreview !== null;
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
    $<HTMLButtonElement>(id).disabled = state.running;
  }
  $<HTMLButtonElement>("#info-retry").disabled = state.running;
  updateBtn.disabled = blocked || state.updateInstalled;
  for (const control of document.querySelectorAll<HTMLButtonElement>(
    "[data-cmd], .scope-tab, #package-open",
  )) control.disabled = blocked || !state.configured || state.restartRequired;
  for (const control of setupBox.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
    "input, button",
  )) control.disabled = blocked || state.restartRequired;
  for (const control of settingsBox.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
    "input, button:not(#settings-close):not(#settings-retry)",
  )) control.disabled = blocked || state.settingsLoading || state.settingsInfo === null;
  settingsRetry.disabled = blocked || state.settingsLoading;
  $<HTMLButtonElement>("#settings-open").disabled = blocked || state.restartRequired;

  const gate: ControlGate = {
    blocked,
    configured: state.configured,
    restartRequired: state.restartRequired,
    managementBlocked: blocked || !state.configured || state.restartRequired,
  };
  for (const handler of syncHandlers) handler(gate);
}

export function setBusy(busy: boolean, label = ""): void {
  state.running = busy;
  feedback(operationStatus, busy ? `${label}，請稍候…` : "");
  $(".app").setAttribute("aria-busy", String(busy));
  syncControls();
}

export async function cancelPreview(): Promise<boolean> {
  if (state.running) return false;
  const pending = state.pendingPreview;
  if (!pending) return true;
  setBusy(true, "取消預覽");
  try {
    const result = await api()!.cancel_preview(pending.token);
    if (result.code !== 0) { presentResult(result); return false; }
    state.pendingPreview = null;
    confirmBox.hidden = true;
    outputState.textContent = pending.kind === "push"
      ? "已取消確認，尚未上傳；已收集的差異仍保留" : "已取消確認，尚未套用";
    outputState.className = "output-state";
    state.previewOpener?.focus();
    return true;
  } catch (error) { presentResult(errorResult(error)); return false; }
  finally { setBusy(false); }
}

export function viewFocusTarget(view: MainView): HTMLElement {
  switch (view) {
    case "apply": return $("#apply-title");
    case "memory": return $("#memory-title");
    case "output":
      return outputTitle;
    case "skills":
      return $("#skills-title");
    case "export":
      return $("#export-title");
    case "status":
      return state.configured ? heroTitle : $(".setup-title");
  }
}


/** Run when a view is left, so the shell need not know what a view owns.
 *
 * showView used to call into the apply panel directly, which would have
 * made this file import a feature that imports it back.
 */
const leaveHandlers = new Map<MainView, () => void>();
export function onLeave(view: MainView, handler: () => void): void {
  leaveHandlers.set(view, handler);
}

export function showView(view: MainView, focus = true): void {
  if (view !== "output" && state.pendingPreview) {
    void cancelPreview().then(cancelled => {
      if (cancelled) { showView(view, false); state.previewOpener?.focus(); }
    });
    return;
  }
  if (state.currentView !== view && view !== "output") leaveHandlers.get(state.currentView)?.();
  closeActiveSelect();
  state.currentView = view;
  $("#hero").hidden = view !== "status" || !state.configured;
  setupBox.hidden = view !== "status" || state.configured;
  $("#output").hidden = view !== "output";
  $("#package").hidden = view !== "skills";
  $("#package-result").hidden = view !== "export";
  $("#apply-panel").hidden = view !== "apply";
  $("#memory-panel").hidden = view !== "memory";
  $(".app").scrollTop = 0;
  if (focus) {
    const target = viewFocusTarget(view);
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
}

export function goBack(): void {
  if (state.running) return;
  if (state.pendingPreview) {
    void closePreview();
    return;
  }
  const previous = state.currentView;
  if (previous === "status") return;

  let target: MainView = "status";
  if (previous === "output") {
    target = state.outputReturn;
  } else if (previous === "export") {
    target = "skills";
  }
  showView(target);

  let opener: HTMLElement | null = null;
  switch (previous) {
    case "memory":
      opener = $("#memory-open");
      break;
    case "skills":
      opener = $("#package-open");
      break;
    case "apply":
      opener = $("[data-cmd=apply]");
      break;
    case "export":
      opener = $("#skill-package");
      break;
  }
  opener?.focus({ preventScroll: true });
}

export function openOutput(): void {
  if (state.currentView !== "output") state.outputReturn = state.currentView;
  showView("output");
}

export function presentResult(result: RunResult): void {
  const details = [result.output || "（沒有輸出）"];
  if ("error" in result && result.error) details.push(`錯誤：${result.error}`);
  if ("backup_path" in result && result.backup_path) details.push(`備份位置：${result.backup_path}`);
  if ("recovery_required" in result && result.recovery_required) details.push("需要人工復原，請保留備份並檢查上述錯誤。");
  renderOutput(details.join("\n"));
  outputState.textContent = result.code === 0 ? "完成" : "未完成";
  outputState.className = `output-state ${result.code === 0 ? "is-ok" : "is-fail"}`;
  outputBody.scrollTop = 0;
}

export function errorResult(error: unknown): RunResult {
  return { code: 1, output: `✗ 操作失敗：${String(error)}` };
}

export async function perform<T extends RunResult>(
  label: string,
  task: () => Promise<T>,
  reveal = true,
): Promise<T | RunResult | null> {
  if (state.running || state.pendingPreview || !api()) return null;
  if (reveal) openOutput();
  $("#preview-changes").hidden = true;
  $("#pull-apply").hidden = true;
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
export function localizeOutputLine(raw: string): string | null {
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

export let lastOutput = "";
export function setLastOutput(value: string): void { lastOutput = value; }

export async function copyText(text: string): Promise<boolean> {
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

export function renderOutput(text: string): void {
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
    else if (line.startsWith("═══")) {
      span.className = "line-head";
      // 狀態頁點「有差異」要能跳到這一段
      const tool = /^═+\s*Status:\s*(\S+)/.exec(line)?.[1];
      if (tool) span.id = `output-status-${tool}`;
    }
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

export function showPlaceholder(text: string): void {
  lastOutput = "";
  outputCopy.hidden = true;
  const span = document.createElement("span");
  span.className = "line-placeholder";
  span.textContent = text;
  outputBody.replaceChildren(span);
}

export function installMessage(zips: string[]): string {
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

export function firstErrorLine(output: string): string {
  for (const raw of output.split("\n")) {
    const line = raw.trim();
    if (/^[✗⚠]/.test(line)) {
      return line.replace(/^[✗⚠]\s*/, "");
    }
  }
  return "請查看詳細結果。";
}

export function armPreview(pending: PendingPreview, note: string): void {
  state.pendingPreview = pending;
  confirmBox.hidden = false;
  $("#confirm-text").textContent = note;
  confirmYes.textContent = `確認${pending.label}`;
  outputState.textContent = "等你確認";
  outputState.className = "output-state is-review";
  syncControls();
  $("#confirm-text").focus();
}

export async function closePreview(): Promise<void> {
  if (await cancelPreview()) {
    showView(state.outputReturn, false);
    state.previewOpener?.focus();
  }
}

