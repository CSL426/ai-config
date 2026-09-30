/** The automation page: what this machine does on its own — the daily
 * memory push, the daily tool update, the usage-window keepalive, the
 * handoff reminder, and which tools write into the project journal.
 *
 * Its state comes with memory_info, so the page renders through
 * onMemoryInfo and every switch ends by asking memory.ts for a reload.
 */

import { $, REMEMBER_HOSTS } from "./dom";
import { state } from "./state";
import { api, feedback, goBack, onSync, perform, showView } from "./shell";
import { onMemoryInfo, refreshMemory } from "./memory";
import type { MemoryInfo, RememberHost, RememberHostState } from "./bridge";

const notice = $("#automation-feedback");

onSync(({ managementBlocked }) => {
  for (const control of document.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
    "#handoff-reminder-toggle, #handoff-reminder-threshold, #handoff-reminder-save",
  )) {
    control.disabled = managementBlocked || state.memoryLoading || !state.memoryInfo?.handoff_reminder
      || Boolean(state.memoryInfo.handoff_reminder.reason);
  }
  $<HTMLButtonElement>("#handoff-reminder-save").disabled ||= !state.memoryInfo?.handoff_reminder?.enabled;
  for (const host of REMEMBER_HOSTS) {
    $<HTMLInputElement>(`#remember-${host}-toggle`).disabled =
      managementBlocked || state.memoryLoading || !state.memoryInfo?.remember_hosts?.[host]?.available;
  }
  for (const control of document.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
    "#autoupdate-toggle",
  )) {
    control.disabled = managementBlocked || state.memoryLoading;
  }
  $<HTMLButtonElement>("#automation-open").disabled = managementBlocked;
  $<HTMLButtonElement>("#automation-refresh").disabled = managementBlocked || state.memoryLoading;
});

function handoffReminderHint(state: MemoryInfo["handoff_reminder"]): string {
  if (!state) {
    return "目前後端尚未提供交接提醒設定，請更新並重開視窗。";
  }
  if (state.reason) {
    return `無法讀取提醒設定：${state.reason}`;
  }
  if (state.enabled && !state.installed) {
    return "提醒尚未完整安裝，請按更新門檻重新安裝。";
  }
  if (state.enabled) {
    return `Context 用量達 ${state.threshold}% 時提醒交接。`;
  }
  return "目前未啟用。啟用共用記憶時會一併以 70% 開啟。";
}

function renderHandoffReminder(info: MemoryInfo): void {
  const state = info.handoff_reminder;
  $<HTMLInputElement>("#handoff-reminder-toggle").checked = state?.enabled ?? false;
  $<HTMLInputElement>("#handoff-reminder-threshold").value = String(state?.threshold ?? 70);
  $("#handoff-reminder-hint").textContent = handoffReminderHint(state);
}

function rememberHostHint(host: RememberHost, state: RememberHostState | undefined): string {
  if (!state) return "";
  if (!state.available) return host === "codex" ? "這台沒有安裝 Codex。" : "這台沒有安裝 Antigravity。";
  if (state.detail) return state.detail;
  if (!state.installed) {
    return host === "codex"
      ? "安裝 remember 到 Codex；裝完在 Codex 裡輸入 /hooks 看過一次就算信任。"
      : "把 remember 的 hook 合併進 Antigravity 的共用 hooks.json。";
  }
  if (host === "codex" && state.trusted === false) {
    return `已安裝 ${state.version}，但 hook 還沒信任：在 Codex 輸入 /hooks 看過一次。`;
  }
  return host === "codex"
    ? `已安裝 ${state.version}，Codex 的工作會進專案日誌。`
    : `已安裝（腳本 ${state.version}），Antigravity 的工作會進專案日誌。`;
}

function renderRememberHosts(info: MemoryInfo): void {
  for (const host of REMEMBER_HOSTS) {
    const state = info.remember_hosts?.[host];
    $<HTMLInputElement>(`#remember-${host}-toggle`).checked = state?.installed ?? false;
    $(`#remember-${host}-hint`).textContent = rememberHostHint(host, state);
  }
}

function renderKeepalive(info: MemoryInfo): void {
  const toggle = $<HTMLInputElement>("#keepalive-toggle");
  const state = info.keepalive ?? {
    installed: false, times: [], model: "", ccs: "", recent: [], tools: {},
  };
  toggle.checked = state.installed;
  renderKeepaliveWindow(state);
  const hint = state.ccs
    ? `claude-scheduler 的排程還在（${state.ccs}），兩個都開會一天點兩次火。`
    : "在選定的時間對 Claude、Codex、Antigravity 各送一句即丟的提示，讓五小時視窗的邊界避開工作時段。";
  $("#keepalive-hint").textContent = hint;

  const row = $("#keepalive-times-row");
  row.hidden = !state.installed;
  if (!state.installed) return;
  $<HTMLInputElement>("#keepalive-times").value = (state.times ?? []).join(" ");
  // 三個工具各有自己的視窗,狀態一起列出來,免得以為只有 Claude 有
  const tools = state.tools ?? {};
  const summary = Object.keys(tools).map((name) => {
    const one = tools[name];
    return one.installed ? `${name} ${one.times.join(" ")}` : `${name} 未啟用`;
  });
  const recent = state.recent ?? [];
  $("#keepalive-recent").textContent = [
    summary.length ? summary.join("；") : "",
    recent.length ? `最近：${recent[recent.length - 1]}` : "還沒有執行紀錄。",
  ].filter(Boolean).join(" · ");
}

function renderKeepaliveWindow(state: MemoryInfo["keepalive"]): void {
  // Claude Code 回報的實際視窗;不是從排程時間開始,就是那次呼叫沒錨定到
  const window = state.window ?? null;
  $("#keepalive-window").textContent = window
    ? `目前視窗 ${window.start}–${window.reset}`
    : "";
  $("#keepalive-drift").textContent = window?.drift
    ? `視窗不是從排程的 ${window.drift} 開始：那次呼叫落在別人開的視窗裡，同帳號在更早的時間有其他用量。`
    : "";
  // 一個工具有好幾個帳號時(codex 的 ~/.codex-set、~/.codex-csl),各帳號結果分開列
  const list = $("#keepalive-accounts");
  list.replaceChildren();
  for (const [tool, one] of Object.entries(state.tools ?? {})) {
    for (const [home, line] of Object.entries(one.accounts ?? {})) {
      const item = document.createElement("li");
      const label = document.createElement("code");
      label.textContent = `${tool} ${home}`;
      item.append(label, `：${line}`);
      list.append(item);
    }
  }
}


function localTime(stamp: string): string {
  const parsed = new Date(stamp);
  if (Number.isNaN(parsed.getTime())) return stamp;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} `
    + `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function renderAutopush(info: MemoryInfo): void {
  const toggle = $<HTMLInputElement>("#autopush-toggle");
  const state = info.autopush ?? {
    installed: false, last_push: "", reason: "", slot: "", host: "", others: [],
  };
  toggle.checked = state.installed;
  const parts: string[] = ["沒有變更或十二小時內推過就跳過。"];
  if (state.last_push) {
    parts.push(`上次上傳：${localTime(state.last_push)}`);
  }
  $("#autopush-hint").textContent = parts.join(" ");
  // 只顯示成功時間的話,被擋下的那一晚看起來一切正常
  const failure = state.last_failure ?? null;
  const failed = $("#autopush-failure");
  failed.hidden = !failure;
  failed.textContent = failure
    ? `上次自動上傳失敗（${localTime(failure.when)}）：${failure.reason}`
      + (failure.paths.length ? ` ${failure.paths.join("、")}` : "")
      + "。處理後按記憶頁的「上傳記憶」，成功就會清掉這則。"
    : "";

  // 時段屬於每晚排程,只開自動更新時也要能改
  const scheduled = state.scheduled ?? state.installed;
  const row = $("#autopush-slot-row");
  row.hidden = !scheduled;
  if (!scheduled) return;
  $<HTMLInputElement>("#autopush-slot").value = state.slot || "04:00";
  const others = state.others ?? [];
  $("#autopush-others").textContent = others.length
    ? `其他機器：${others.map((o) => `${o.host} ${o.slot}`).join("、")}`
    : "目前只有這台登記了時間。多台時會各自錯開，不必手動協調。";
}


type AutoupdateStep = NonNullable<MemoryInfo["autoupdate"]>["steps"][number];

function autoupdateLine(step: AutoupdateStep): string {
  const text = step.note
    || (step.before && step.after && step.before !== step.after
      ? `${step.before} → ${step.after}`
      : `${step.after || step.before} 已是最新`);
  const freed = Math.floor((step.freed ?? 0) / 2 ** 20);
  const kept = step.kept ?? 0;
  return text
    + (freed ? `（清掉舊執行檔 ${freed} MB）` : "")
    + (kept ? `（${kept} 個舊執行檔使用中，下次再清）` : "");
}

function renderAutoupdate(info: MemoryInfo): void {
  const state = info.autoupdate ?? { installed: false, time: "", last_run: "", steps: [] };
  $<HTMLInputElement>("#autoupdate-toggle").checked = state.installed;
  // 沒更新成功的工具要看得到,不然一台落後好幾版也沒人發現
  const failed = state.steps.filter((step) => !step.ok);
  const warning = $("#autoupdate-failure");
  warning.hidden = failed.length === 0;
  warning.textContent = failed.length
    ? `上次自動更新有工具失敗（${localTime(state.last_run)}）：`
      + failed.map((step) => `${step.name} ${step.note}`).join("；")
      + "。處理後在終端機執行 acg autoupdate run，全部成功就會清掉這則。"
    : "";

  $("#autoupdate-hint").textContent = state.installed && state.time
    ? `每晚 ${state.time} 先更新 Claude Code、Codex、Antigravity 和 acg，再用新版上傳記憶；時間在上方「每晚排程的時間」改。`
    : "每晚先更新 Claude Code、Codex、Antigravity 和 acg，再用新版上傳記憶；沒裝的略過，一個失敗不影響其他。";
  const row = $("#autoupdate-steps-row");
  row.hidden = !state.installed;
  if (!state.installed) return;
  const list = $("#autoupdate-steps");
  list.replaceChildren();
  for (const step of state.steps) {
    const item = document.createElement("li");
    const label = document.createElement("code");
    label.textContent = step.name;
    item.append(label, `${step.ok ? "" : " ✗"}：${autoupdateLine(step)}`);
    list.append(item);
  }
  if (!state.steps.length) {
    const item = document.createElement("li");
    item.textContent = "還沒有執行紀錄。";
    list.append(item);
  } else {
    const item = document.createElement("li");
    item.textContent = `上次執行：${localTime(state.last_run)}`;
    list.prepend(item);
  }
}

async function configureAutoupdate(wanted: boolean): Promise<void> {
  const bridge = api();
  const toggle = $<HTMLInputElement>("#autoupdate-toggle");
  const previous = state.memoryInfo?.autoupdate?.installed ?? false;
  if (!bridge || state.running || state.pendingPreview) {
    toggle.checked = previous;
    return;
  }
  const result = await perform(wanted ? "排定自動更新" : "取消自動更新", async () => {
    const response = await bridge.set_autoupdate(wanted);
    if (response.code === 0) await refreshMemory();
    return response;
  }, false);
  if (!result || result.code !== 0) toggle.checked = previous;
  if (result) feedback(notice, result.output, result.code !== 0);
}

$<HTMLInputElement>("#autoupdate-toggle").addEventListener("change", (event) => {
  void configureAutoupdate((event.currentTarget as HTMLInputElement).checked);
});

async function configureHandoffReminder(enabled: boolean): Promise<void> {
  const bridge = api();
  const toggle = $<HTMLInputElement>("#handoff-reminder-toggle");
  const input = $<HTMLInputElement>("#handoff-reminder-threshold");
  const previous = state.memoryInfo?.handoff_reminder?.enabled ?? false;
  const threshold = input.valueAsNumber;
  if (!bridge || state.running || state.pendingPreview || !input.reportValidity()
      || !Number.isInteger(threshold)) {
    toggle.checked = previous;
    return;
  }
  const result = await perform("設定交接提醒", async () => {
    const response = await bridge.set_handoff_reminder(enabled, threshold);
    if (response.code === 0) await refreshMemory();
    return response;
  }, false);
  if (!result || result.code !== 0) toggle.checked = previous;
  if (result) feedback(notice, result.output, result.code !== 0);
}

$<HTMLInputElement>("#handoff-reminder-toggle").addEventListener("change", (event) => {
  void configureHandoffReminder((event.currentTarget as HTMLInputElement).checked);
});
$("#handoff-reminder-save").addEventListener("click", () => {
  void configureHandoffReminder(true);
});

async function configureRememberHost(host: RememberHost, enabled: boolean): Promise<void> {
  const bridge = api();
  const toggle = $<HTMLInputElement>(`#remember-${host}-toggle`);
  const previous = state.memoryInfo?.remember_hosts?.[host]?.installed ?? false;
  if (!bridge || state.running || state.pendingPreview) {
    toggle.checked = previous;
    return;
  }
  const result = await perform(enabled ? "安裝 remember" : "移除 remember", async () => {
    const response = await bridge.set_remember_host(host, enabled);
    if (response.code === 0) await refreshMemory();
    return response;
  }, false);
  if (!result || result.code !== 0) toggle.checked = previous;
  if (result) feedback(notice, result.output, result.code !== 0);
}

for (const host of REMEMBER_HOSTS) {
  $<HTMLInputElement>(`#remember-${host}-toggle`).addEventListener("change", (event) => {
    void configureRememberHost(host, (event.currentTarget as HTMLInputElement).checked);
  });
}

$<HTMLInputElement>("#keepalive-toggle").addEventListener("change", async (event) => {
  const toggle = event.currentTarget as HTMLInputElement;
  const bridge = api();
  const wanted = toggle.checked;
  if (!bridge) { toggle.checked = !wanted; return; }
  toggle.disabled = true;
  try {
    const result = await bridge.set_keepalive(wanted);
    if (result.code !== 0) {
      toggle.checked = !wanted;
      feedback(notice, result.output, true);
      return;
    }
    feedback(notice, wanted ? "已排定錨定用量視窗" : "已取消錨定");
    await refreshMemory();
  } catch (error) {
    toggle.checked = !wanted;
    feedback(notice, `無法更新排程：${String(error)}`, true);
  } finally {
    toggle.disabled = false;
  }
});

$<HTMLButtonElement>("#keepalive-save").addEventListener("click", async () => {
  const bridge = api();
  const raw = $<HTMLInputElement>("#keepalive-times").value.trim();
  if (!bridge) return;
  const button = $<HTMLButtonElement>("#keepalive-save");
  button.disabled = true;
  try {
    const result = await bridge.set_keepalive(true, raw.split(/[\s,]+/).filter(Boolean));
    feedback(notice, result.output, result.code !== 0);
    if (result.code === 0) await refreshMemory();
  } catch (error) {
    feedback(notice, `無法更新排程：${String(error)}`, true);
  } finally {
    button.disabled = false;
  }
});

$<HTMLInputElement>("#autopush-toggle").addEventListener("change", async (event) => {
  const toggle = event.currentTarget as HTMLInputElement;
  const bridge = api();
  const wanted = toggle.checked;
  if (!bridge) { toggle.checked = !wanted; return; }
  toggle.disabled = true;
  try {
    const result = await bridge.set_autopush(wanted);
    if (result.code !== 0) {
      toggle.checked = !wanted;
      feedback(notice, result.output, true);
      return;
    }
    feedback(notice, wanted ? "已排定每天自動上傳" : "已取消自動上傳");
    await refreshMemory();
  } finally {
    toggle.disabled = false;
  }
});

$<HTMLButtonElement>("#autopush-slot-save").addEventListener("click", async () => {
  const bridge = api();
  const clock = $<HTMLInputElement>("#autopush-slot").value;
  if (!bridge || !clock) return;
  const button = $<HTMLButtonElement>("#autopush-slot-save");
  button.disabled = true;
  try {
    const result = await bridge.set_autopush_slot(clock);
    if (result.code !== 0) {
      feedback(notice, result.output, true);
      return;
    }
    feedback(notice, `這台改到每天 ${clock}`);
    await refreshMemory();
  } finally {
    button.disabled = false;
  }
});

onMemoryInfo((info) => {
  renderAutopush(info);
  renderAutoupdate(info);
  renderKeepalive(info);
  renderHandoffReminder(info);
  renderRememberHosts(info);
});

$("#automation-open").addEventListener("click", () => { showView("automation"); void refreshMemory(); });
$("#automation-back").addEventListener("click", goBack);
$("#automation-refresh").addEventListener("click", () => { void refreshMemory(); });
