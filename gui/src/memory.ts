/** The memory page, the project's memory section, and the apply panel.
 *
 * One backend call (memory_info) feeds the memory page, the project page
 * and the automation page. This file owns that call; the other pages
 * register a renderer with onMemoryInfo instead of importing it back.
 */

import { $, outputState } from "./dom";
import { state } from "./state";
import {
  api,
  feedback,
  goBack,
  onSync,
  perform,
  presentResult,
  showView,
  onLeave,
  armPreview,
  syncControls,
  toolLabel,
} from "./shell";
export let requestPush: (() => Promise<void>) | undefined;
export function setRequestPush(fn: () => Promise<void>): void { requestPush = fn; }

import type { ApplyCategory, ChangePreview, MemoryAction, MemoryInfo } from "./bridge";

const infoRenderers: ((info: MemoryInfo) => void)[] = [];
/** Render another page's part of memory_info whenever it is reloaded. */
export function onMemoryInfo(render: (info: MemoryInfo) => void): void {
  infoRenderers.push(render);
}

/** The feedback line of the page on screen; memory_info serves three pages. */
export function pageFeedback(): HTMLElement {
  const page = state.currentView === "output" ? state.outputReturn : state.currentView;
  if (page === "project") return $("#project-feedback");
  if (page === "automation") return $("#automation-feedback");
  return $("#memory-feedback");
}

onSync(({ managementBlocked }) => {
  for (const selector of ["#memory-open", "#apply-preview", "#memory-refresh", "#memory-push", "#pull-apply"]) {
    $<HTMLButtonElement>(selector).disabled = managementBlocked;
  }
  $<HTMLButtonElement>("#apply-preview").disabled = managementBlocked || !applyCategory();
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-memory-action]")) {
    const action = button.dataset.memoryAction as MemoryAction;
    const permission = state.memoryInfo?.actions[action];
    button.disabled = managementBlocked || state.memoryLoading || !permission?.allowed
      || ((action === "adopt" || action === "release") && !state.projectToken);
    button.title = permission?.reason ?? "請先讀取記憶狀態";
  }
  $<HTMLButtonElement>("#memory-push").disabled = managementBlocked || state.memoryLoading || !state.memoryInfo?.actions.push.allowed;
  for (const button of document.querySelectorAll<HTMLButtonElement>("#memory-locations button, #memory-refresh")) {
    button.disabled = managementBlocked || state.memoryLoading
      || (button.closest("#memory-locations") !== null && !state.memoryInfo);
  }
});

export const MEMORY_LABELS: Record<MemoryAction, string> = {
  enable: "啟用共用記憶", disable: "停用共用記憶",
  adopt: "同步此專案日誌", release: "日誌改存本機",
};

onLeave("apply", () => resetCategories());

export function resetCategories(): void {
  $<HTMLInputElement>("#category-settings").checked = false;
  $<HTMLInputElement>("#category-skills").checked = false;
  syncControls();
}

export function applyCategory(): ApplyCategory | null {
  const settings = $<HTMLInputElement>("#category-settings").checked;
  const chosenSkills = $<HTMLInputElement>("#category-skills").checked;
  return settings && chosenSkills ? "all" : settings ? "settings" : chosenSkills ? "skills" : null;
}

export function openApply(): void {
  if (state.running || state.pendingPreview) return;
  resetCategories();
  $("#apply-scope").textContent = `套用目標：${toolLabel()}`;
  showView("apply");
}

export function textRow(parent: HTMLElement, label: string, value: string): void {
  const row = document.createElement("p");
  const title = document.createElement("strong");
  title.textContent = `${label}：`;
  row.append(title, document.createTextNode(value));
  parent.append(row);
}

export function renderChanges(preview: ChangePreview): void {
  const list = $("#preview-changes");
  list.replaceChildren();
  for (const change of preview.changes) {
    const item = document.createElement("article");
    item.className = "preview-change";
    textRow(item, `${change.tool} / ${change.category}`, change.operation);
    if (change.source) textRow(item, "來源", change.source);
    textRow(item, "目的地", change.destination);
    if (change.physical_target) textRow(item, "實體目標", change.physical_target);
    if (change.shared) textRow(item, "共用影響", "此檔案亦由其他工具使用（包含 Claude）；請確認實體目標。");
    if (change.reason) textRow(item, "原因", change.reason);
    list.append(item);
  }
  for (const warning of preview.warnings) textRow(list, "注意", warning);
  list.hidden = !list.childElementCount;
}

export async function previewChange(kind: "apply" | "memory", action?: MemoryAction): Promise<void> {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview) return;
  const category = applyCategory();
  if (kind === "apply" && !category) return;
  if (kind === "memory" && (!action || !state.memoryInfo?.actions[action].allowed)) return;
  state.previewOpener = document.activeElement as HTMLElement | null;
  const label = kind === "apply" ? `套用 ${toolLabel()} · ${category === "all" ? "設定、獨立技能" : category === "settings" ? "設定" : "獨立技能"}` : MEMORY_LABELS[action!];
  const result = await perform(`${label}前預覽`, () => kind === "apply"
    ? bridge.preview_apply(state.selectedTool, category!)
    : action === "adopt" || action === "release"
      ? bridge.preview_memory(action, state.projectToken!) : bridge.preview_memory(action!));
  if (!result || !("changes" in result)) return;
  const preview = result as ChangePreview;
  renderChanges(preview);
  if (preview.code !== 0) return;
  if (!preview.needs_confirmation) { outputState.textContent = "已一致"; return; }
  if (!preview.token) { presentResult({ code: 1, output: "未取得有效預覽，請重新預覽。" }); return; }
  const count = preview.changes.filter(change => change.operation !== "skip").length;
  armPreview({ kind, token: preview.token, scope: state.selectedTool, label: `${label}（${count} 項變更）` },
    action === "disable" ? "將移除管理區塊與連結。記憶資料保留，既有日誌不搬回；此操作不提交。"
    : action === "release" ? "日誌將改存本機，資料庫會留下 Git 刪除差異；後續上傳會影響其他機器的同步內容。本次不自動上傳。"
    : "請檢閱檔案、連結目標與略過原因。取消不會套用；內容變動後必須重新預覽。");
}

function memoryHealthGroup(
  kind: "warn" | "error",
  heading: string,
  hint: string,
  paths: string[],
): HTMLElement {
  const group = document.createElement("div");
  group.className = "memory-health-group";
  group.dataset.kind = kind;
  const title = document.createElement("p");
  title.className = "memory-health-title";
  title.textContent = `${heading}（${paths.length}）`;
  const note = document.createElement("p");
  note.className = "memory-health-hint";
  note.textContent = hint;
  const list = document.createElement("ul");
  list.className = "memory-health-list";
  for (const path of paths) {
    const item = document.createElement("li");
    item.textContent = path;
    list.append(item);
  }
  group.append(title, note, list);
  return group;
}

function renderHandoffs(info: MemoryInfo): void {
  const threads = info.handoffs ?? [];
  const list = $("#handoff-threads");
  list.replaceChildren();
  for (const thread of threads) {
    const item = document.createElement("li");
    item.className = "handoff-thread";
    item.dataset.stale = String(thread.stale);
    const name = document.createElement("strong");
    name.textContent = thread.thread;
    const meta = document.createElement("span");
    meta.className = "handoff-thread-meta";
    meta.textContent = [
      thread.project,
      thread.age_days ? `${thread.age_days} 天前開的` : "",
      thread.stale ? "⚠ 可能已過期" : "",
    ].filter(Boolean).join(" · ");
    const summary = document.createElement("span");
    summary.className = "handoff-thread-summary";
    summary.textContent = thread.summary;
    item.append(name, meta, summary);
    list.append(item);
  }
  $("#handoff-threads-empty").textContent = threads.length
    ? ""
    : "目前沒有待接手的工作線。";
}

function renderMemoryHealth(info: MemoryInfo): void {
  const host = $("#memory-health");
  host.replaceChildren();
  // 舊版後端沒有這些欄位;讀不到就當成沒有問題,不要讓整頁停在這裡
  const checks: Array<{ kind: "warn" | "error"; heading: string; hint: string; paths: string[] }> = [
    {
      kind: "error",
      heading: "疑似含有憑證的筆記",
      hint: "上傳會被擋下。請先移除內容；本機日誌不同步，不在此列。",
      paths: info.secret_notes ?? [],
    },
    {
      kind: "warn",
      heading: "沒有寫進索引的筆記",
      hint: "索引沒連到它們，下次開新對話不會被讀到。",
      paths: info.index_unlisted ?? [],
    },
    {
      kind: "warn",
      heading: "指向不存在檔案的索引連結",
      hint: "檔案已不在，連結留著只會浪費閱讀的人的時間。",
      paths: info.index_dangling ?? [],
    },
  ];
  const groups = checks
    .filter((c) => c.paths.length > 0)
    .map((c) => memoryHealthGroup(c.kind, c.heading, c.hint, c.paths));

  host.hidden = groups.length === 0;
  host.append(...groups);
}

export async function refreshMemory(): Promise<void> {
  const bridge = api();
  if (!bridge || !state.configured || state.memoryLoading) return;
  state.memoryLoading = true;
  state.memoryInfo = null;
  const notice = pageFeedback();
  // 專案頁的訊息列也放部署的錯誤;讀取中與讀取成功不能把它蓋掉
  const shared = notice.id === "project-feedback";
  if (!shared) feedback(notice, "讀取記憶狀態中…");
  $("#memory-status").textContent = "讀取中…";
  $("#memory-status").dataset.state = "loading";
  $("#memory-summary").textContent = "正在確認共用位置與版本管理狀態…";
  syncControls();
  try {
    let info = await bridge.memory_info(state.projectToken ?? undefined);
    const stale = info.code !== 0 && info.error === "STALE_PREVIEW" && state.projectToken !== null;
    if (stale) {
      // 選過的專案搬走了:專案頁要重選,但共用記憶與自動化不該跟著讀不到
      state.projectToken = null;
      $("#project-root").textContent = "尚未選擇專案。";
      feedback($("#project-feedback"), info.output, true);
      info = await bridge.memory_info();
    }
    if (info.code !== 0) {
      feedback(notice, `${info.error ?? ""} ${info.output}`, true);
      return;
    }
    state.memoryInfo = info;
    if (!shared) feedback(notice, "");
    const status = $("#memory-status");
    let statusText: string;
    if (info.shared_status === "ok") {
      statusText = "共用位置已連結";
    } else if (info.shared_status === "conflict") {
      statusText = "共用位置需處理";
    } else {
      statusText = "尚未啟用連結";
    }
    status.textContent = statusText;
    status.dataset.state = info.shared_status === "ok" ? "success" : "warning";

    let summaryText: string;
    if (!info.tracked) {
      summaryText = "記憶尚未納入 Git；上傳前可先預覽將納管的檔案。";
    } else if (info.git_status === "clean") {
      summaryText = "記憶已納入 Git，目前沒有本機檔案變更。";
    } else {
      summaryText = `記憶已納入 Git，有 ${info.changed_paths.length} 個檔案變更，準備好後可預覽上傳。`;
    }
    $("#memory-summary").textContent = summaryText;
    renderMemoryHealth(info);
    renderHandoffs(info);
    for (const render of infoRenderers) render(info);
    const data = $("#memory-data");
    data.replaceChildren();
    textRow(data, "資料根", info.data_root);
    textRow(data, "共用位置", info.shared_path);
    textRow(data, "目錄狀態", info.shared_status);
    textRow(data, "Git", `${info.git_status} · ${info.tracked ? "已納管" : "未納管"}`);
    for (const path of info.changed_paths) textRow(data, "變更", path);
    const entries = $("#memory-entries");
    entries.replaceChildren();
    for (const entry of info.entries) {
      const item = document.createElement("div");
      item.className = "memory-entry";
      item.dataset.state = entry.status;
      const heading = document.createElement("strong");
      heading.textContent = toolLabel(entry.tool);
      const state = document.createElement("span");
      state.className = "memory-entry-state";
      let stateLabel: string;
      if (entry.status === "installed") {
        stateLabel = "規則已安裝，請開新會話驗證";
      } else if (entry.status === "blocked") {
        stateLabel = "入口受阻";
      } else {
        stateLabel = "規則未安裝";
      }
      state.textContent = stateLabel;
      item.append(heading, state);
      textRow(item, "CLI", entry.cli_installed ? "已安裝" : "未安裝");
      const details = document.createElement("details");
      details.className = "memory-details";
      const summary = document.createElement("summary");
      summary.textContent = "規則位置";
      details.append(summary);
      textRow(details, "", entry.path);
      item.append(details);
      if (entry.reason) textRow(item, "原因", entry.reason);
      entries.append(item);
    }
    const project = $("#memory-project");
    project.replaceChildren();
    if (info.project) {
      textRow(project, "本機專案", info.project.root);
      textRow(project, "專案鍵值", `${info.project.key}${info.project.stable ? "" : "（無遠端，鍵值可能因重新命名而改變）"}`);
      textRow(project, "專案記憶", info.project.memory_path);
      textRow(project, "日誌", `${info.project.journal_path} · ${info.project.journal_status}`);
      textRow(project, "remember", info.project.remember_installed ? "已安裝" : "未安裝，無法同步新日誌；已同步日誌仍可改存本機");
    } else project.textContent = "尚未選擇專案。";
    for (const [id, actions] of [
      ["#memory-global-reasons", ["enable", "disable", "push"]],
      ["#memory-project-reasons", ["adopt", "release"]],
    ] as const) {
      $(id).textContent = actions.map(action => info.actions[action].reason).filter(Boolean).join("；");
    }
    const locations = $("#memory-locations");
    locations.replaceChildren();
    for (const location of info.locations) {
      const row = document.createElement("div");
      row.className = "memory-location";
      const button = document.createElement("button");
      button.className = "btn btn-ghost";
      button.textContent = `開啟${location.label}`;
      button.addEventListener("click", async () => {
        const result = await perform(`開啟${location.label}`, () => bridge.open_memory_location(location.token), false);
        if (result) feedback($("#memory-feedback"), result.output, result.code !== 0);
      });
      const path = document.createElement("span");
      path.textContent = location.path;
      row.append(button, path);
      locations.append(row);
    }
  } catch (error) { feedback(notice, `無法讀取記憶狀態：${String(error)}`, true); }
  finally {
    state.memoryLoading = false;
    if (!state.memoryInfo) {
      $("#memory-status").textContent = "讀取失敗";
      $("#memory-status").dataset.state = "error";
      $("#memory-summary").textContent = "目前無法確認記憶狀態，請重新整理。";
    }
    syncControls();
  }
}

$("#apply-back").addEventListener("click", goBack);
$("#pull-apply").addEventListener("click", openApply);
$("#apply-preview").addEventListener("click", () => { void previewChange("apply"); });
for (const id of ["#category-settings", "#category-skills"]) {
  $(id).addEventListener("change", syncControls);
}
$("#memory-open").addEventListener("click", () => { showView("memory"); void refreshMemory(); });
$("#memory-back").addEventListener("click", goBack);
$("#memory-refresh").addEventListener("click", () => { void refreshMemory(); });
$("#memory-push").addEventListener("click", () => { void void requestPush?.(); });
for (const button of document.querySelectorAll<HTMLButtonElement>("[data-memory-action]")) {
  button.addEventListener("click", () => { void previewChange("memory", button.dataset.memoryAction as MemoryAction); });
}
