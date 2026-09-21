/** The status page: the hero verdict, the per-tool tabs, and the four
 * commands those tabs run.
 *
 * It reaches into memory and skills after a command changes something,
 * and neither reaches back, so the imports only point one way.
 */

import {
  $, COMMAND_LABELS, appNotice, confirmBox, confirmYes, heroMark, heroSub,
  heroTitle, outputState, pluginCopy, pluginInstall, toolRows, toolTabs,
  updateBtn,
} from "./dom";
import { state } from "./state";
import {
  api, armPreview, closePreview, copyText, feedback, firstErrorLine,
  lastOutput,
  openOutput, perform, presentResult, renderOutput,
  syncControls, toolLabel,
} from "./shell";
import { openApply, refreshMemory, setRequestPush } from "./memory";
import { loadSkills, setRequestStale } from "./skills";
import { offerLogin } from "./connection";
import type { AcgCommand, PushScope, RunResult, ToolScope } from "./bridge";

export function setHero(mark: "ok" | "pending" | "fail" | "none", title: string, sub: string): void {
  heroMark.className = mark === "none" ? "hero-mark" : `hero-mark is-${mark}`;
  heroTitle.textContent = title;
  heroSub.textContent = sub;
}

export function invalidateToolStates(label = "需重新檢查"): void {
  lastStatus = null;
  stopHeroClock();
  for (const row of toolRows) {
    row.className = "tool-row";
    row.querySelector<HTMLElement>(".tool-state")!.textContent = label;
  }
  setHero("none", "尚待確認", "設定可能已改變，請重新檢查工具與本機保存設定是否一致。");
}

// ── 上次檢查的時間 ─────────────────────
// 檢查過的結果不該一按別的按鈕就被清成「需重新檢查」；保留結果，標上多久以前，
// 有操作可能改變它時再加一句建議。

let lastStatus: { at: number; mark: "ok" | "pending"; title: string; sub: string; stale: string } | null = null;
let heroClock: number | null = null;

function formatAgo(since: number): string {
  const seconds = Math.max(0, Math.round((Date.now() - since) / 1000));
  if (seconds < 10) return "剛剛";
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} 分鐘前`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} 小時前` : `${Math.round(hours / 24)} 天前`;
}

export function stopHeroClock(): void {
  if (heroClock !== null) { window.clearInterval(heroClock); heroClock = null; }
}

function renderHeroClock(): void {
  if (!lastStatus) return;
  const when = `${formatAgo(lastStatus.at)}檢查`;
  if (lastStatus.stale) {
    setHero("none", lastStatus.title, `${when}；${lastStatus.stale}，結果可能已改變，建議重新檢查。`);
  } else {
    setHero(lastStatus.mark, lastStatus.title, `${lastStatus.sub}（${when}）`);
  }
}

function rememberStatus(mark: "ok" | "pending", title: string, sub: string): void {
  lastStatus = { at: Date.now(), mark, title, sub, stale: "" };
  stopHeroClock();
  heroClock = window.setInterval(renderHeroClock, 15000);
  renderHeroClock();
}

setRequestStale((reason) => markStale(reason));

export function markStale(reason: string): void {
  if (!lastStatus) { invalidateToolStates(); return; }
  lastStatus.stale = reason;
  renderHeroClock();
}

export function updateStatus(result: RunResult, tool: string): void {
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
    rememberStatus("pending", `${scope}尚未全部確認`, "有工具缺少設定或未取得檢查結果，請查看詳細輸出。");
  } else if (pending) {
    rememberStatus("pending", `${pending} 個工具有差異`, "比較的是工具設定與本機保存設定；請查看差異再決定如何同步。");
  } else {
    rememberStatus("ok", `${scope}設定一致`, "與本機保存的設定一致；尚未檢查雲端是否有新版本。");
  }
}

export async function runCommand(cmd: AcgCommand): Promise<void> {
  const bridge = api();
  if (!bridge || state.running || !state.configured || state.pendingPreview || state.restartRequired) return;
  if (cmd === "apply") { openApply(); return; }
  const tool = cmd === "pull" ? "all" : state.selectedTool;
  if (cmd === "status") invalidateToolStates("檢查中…");
  setHero("none", `${COMMAND_LABELS[cmd]}中…`, `操作範圍：${toolLabel(tool)}`);
  const result = await perform(`${COMMAND_LABELS[cmd]}（${toolLabel(tool)}）`,
    () => bridge.run(cmd, tool), cmd !== "status");
  if (!result) return;
  if (cmd === "status") updateStatus(result, tool);
  else {
    markStale(`剛執行${COMMAND_LABELS[cmd]}`);
    if (result.code !== 0) setHero("fail", "操作未完成", firstErrorLine(result.output));
  }
  if (cmd === "pull") {
    await refreshMemory();
    if (result.code !== 0) offerLogin(result.output);
    if (result.code === 0) {
      const status = await bridge.run("status", state.selectedTool);
      updateStatus(status, state.selectedTool);
      $("#pull-apply").hidden = false;
      feedback(appNotice, "已下載整個資料庫。共用記憶已更新；設定與獨立技能可另行套用，取消套用不會回復下載。");
    }
  }
  if (result.code !== 0) openOutput();
}


setRequestPush(() => previewPush("memory"));

export async function previewPush(scope: PushScope = state.selectedTool): Promise<void> {
  const bridge = api();
  if (!bridge || !state.configured || state.restartRequired) return;
  state.previewOpener = document.activeElement as HTMLElement | null;
  const label = scope === "memory" ? "上傳記憶" : `上傳變更（${toolLabel(scope)}）`;
  const result = await perform(`${label}前預覽`, () => bridge.preview_push(scope));
  if (result && result.code !== 0) offerLogin(result.output);
  if (!result || result.code !== 0) return;
  if (!("needs_confirmation" in result) || !result.needs_confirmation) {
    outputState.textContent = "沒有待上傳內容";
    return;
  }
  if (!("token" in result) || typeof result.token !== "string" || !result.token) {
    presentResult({ code: 1, output: "✗ 未取得有效預覽，請返回後重新預覽。" });
    return;
  }
  if ("changed_paths" in result && Array.isArray(result.changed_paths)) {
    renderOutput(`${lastOutput}\n待提交檔案：\n${result.changed_paths.join("\n")}`);
  }
  if ("outgoing_commits" in result && Array.isArray(result.outgoing_commits)) {
    renderOutput(`${lastOutput}\n完整待推送提交範圍：\n${result.outgoing_commits.join("\n") || "（無既有提交）"}`);
  }
  armPreview({ kind: "push", token: result.token, scope, label }, scope === "memory"
    ? "請檢閱記憶差異與完整待推送提交範圍。確認後才提交及上傳。"
    : "已收集本機設定，尚未提交或上傳。取消仍保留已收集的差異；請檢閱完整待推送提交範圍。");
}

for (const tab of toolTabs) {
  tab.addEventListener("click", () => {
    if (state.running || state.pendingPreview) return;
    state.selectedTool = (tab.dataset.tool ?? "all") as ToolScope;
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
  const pending = state.pendingPreview;
  if (!bridge || !pending || state.running || state.currentView !== "output") return;
  state.pendingPreview = null;
  confirmBox.hidden = true;
  markStale(pending.label);
  const result = await perform(pending.label, () => pending.kind === "push"
    ? bridge.confirm_push(pending.scope, pending.token)
    : pending.kind === "apply" ? bridge.confirm_apply(pending.token)
    : bridge.confirm_memory(pending.token));
  if (result && "error" in result && result.error === "BUSY") {
    armPreview(pending, "另一項操作執行中，此預覽仍有效，請稍後確認或取消。");
    return;
  }
  await refreshMemory();
  if (pending.kind !== "memory") {
    try { updateStatus(await bridge.run("status", state.selectedTool), state.selectedTool); }
    catch { invalidateToolStates(); }
  }
  if (pending.kind === "apply") await loadSkills();
  if (result?.code === 0 && pending.kind === "memory") {
    renderOutput(`${lastOutput}\n已重新整理各入口狀態。規則安裝後請開新會話驗證；本次未提交或上傳。`);
  }
});
$("#confirm-no").addEventListener("click", () => { void closePreview(); });



pluginCopy.addEventListener("click", async () => {
  pluginInstall.select();
  const copied = await copyText(pluginInstall.value);
  if (!copied) { pluginInstall.focus(); pluginInstall.select(); }
  pluginCopy.textContent = copied ? "已複製" : "請按複製快捷鍵";
  setTimeout(() => { pluginCopy.textContent = "複製安裝指令"; }, 2000);
});

updateBtn.addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview || state.updateInstalled) return;
  if (state.pendingUpdate) {
    const result = await perform(`更新到 v${state.pendingUpdate}`, () => bridge.run_update());
    if (result?.code === 0) {
      state.updateInstalled = true;
      state.pendingUpdate = null;
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
        state.pendingUpdate = result.latest;
        updateBtn.textContent = `更新到 v${state.pendingUpdate}`;
        feedback(appNotice, `可更新到 v${state.pendingUpdate}，按上方更新按鈕開始安裝。`);
      }
    } else if (result) openOutput();
  }
  syncControls();
});
$("#config-info").addEventListener("click", () => {
  const bridge = api();
  if (bridge) void perform("連線資訊", () => bridge.config_info());
});

