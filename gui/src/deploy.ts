/** The project deploy panel: install picks into one project, and take them back.
 *
 * The page never names a path itself. Choosing a folder hands back a token
 * from the backend, and every preview and confirm goes through it, the
 * same way the memory page treats its project.
 */

import { $, outputState } from "./dom";
import { state } from "./state";
import {
  api, armPreview, feedback, goBack, onSync, perform, setBusy, showView, syncControls,
} from "./shell";
import { renderChanges } from "./memory";
import type { ChangePreview, DeployItem, DeployKind } from "./bridge";

const GROUPS: { kind: DeployKind; title: string }[] = [
  { kind: "skill", title: "技能" },
  { kind: "plugin", title: "Claude plugin" },
  { kind: "memory", title: "共用記憶" },
  { kind: "claude", title: "Claude 規則與指令" },
];

const feedbackEl = $("#deploy-feedback");

onSync(({ managementBlocked }) => {
  const busy = managementBlocked;
  $<HTMLButtonElement>("#deploy-open").disabled = busy;
  $<HTMLButtonElement>("#deploy-select").disabled = busy;
  for (const box of document.querySelectorAll<HTMLInputElement>("#deploy-items input")) {
    box.disabled = busy || !state.deployToken;
  }
  $<HTMLButtonElement>("#deploy-preview").disabled =
    busy || !state.deployToken || state.deploySelected.size === 0;
  $<HTMLButtonElement>("#deploy-clear").disabled = busy || state.deploySelected.size === 0;
  const placed = state.deployInfo?.deployed;
  $<HTMLButtonElement>("#deploy-remove").disabled = busy || !state.deployToken
    || !placed || (placed.files === 0 && placed.plugins.length === 0 && !placed.memory);
  $("#deploy-selection").textContent = `已選 ${state.deploySelected.size} 項`;
});

function itemRow(item: DeployItem): HTMLLabelElement {
  const label = document.createElement("label");
  label.className = "skill-item";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.value = item.name;
  box.checked = state.deploySelected.has(item.name);
  box.addEventListener("change", () => {
    if (box.checked) state.deploySelected.add(item.name);
    else state.deploySelected.delete(item.name);
    syncControls();
  });
  const name = document.createElement("span");
  name.className = "skill-name";
  name.textContent = item.kind === "skill" ? item.name.slice("skills/".length)
    : item.kind === "plugin" ? item.name.slice("plugins/".length)
    : item.kind === "memory" ? "記憶規則（寫進 AGENTS.md）" : item.name;
  const tag = document.createElement("span");
  tag.className = "skill-tag";
  tag.textContent = item.note;
  label.append(box, name, tag);
  return label;
}

function renderItems(): void {
  const container = $("#deploy-items");
  container.replaceChildren();
  const items = state.deployInfo?.items ?? [];
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "package-hint";
    empty.textContent = "資料庫裡沒有可以部署的內容。";
    container.append(empty);
  }
  for (const group of GROUPS) {
    const members = items.filter(item => item.kind === group.kind);
    if (!members.length) continue;
    const fieldset = document.createElement("fieldset");
    fieldset.className = "deploy-group";
    const legend = document.createElement("legend");
    legend.textContent = `${group.title}（${members.length}）`;
    const list = document.createElement("div");
    list.className = "package-list";
    list.append(...members.map(itemRow));
    fieldset.append(legend, list);
    container.append(fieldset);
  }
  // 資料庫裡已經沒有的選取不能留著,預覽會被拒絕
  const names = new Set(items.map(item => item.name));
  for (const name of [...state.deploySelected]) if (!names.has(name)) state.deploySelected.delete(name);
}

function renderPlaced(): void {
  const placed = state.deployInfo?.deployed;
  const text = $("#deploy-placed");
  if (!state.deployToken || !placed) { text.textContent = "尚未選擇專案。"; return; }
  const parts: string[] = [];
  if (placed.files) parts.push(`${placed.files} 個檔案`);
  if (placed.plugins.length) parts.push(`plugin：${placed.plugins.join("、")}`);
  if (placed.memory) parts.push("AGENTS.md 裡的記憶規則");
  text.textContent = parts.length ? parts.join("；") : "acg 還沒有放任何東西進這個專案。";
}

export async function refreshDeploy(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  try {
    const info = await bridge.deploy_info(state.deployToken ?? undefined);
    if (info.code !== 0) {
      if (info.error === "STALE_PREVIEW") {
        state.deployToken = null;
        $("#deploy-root").textContent = "尚未選擇專案。";
      }
      feedback(feedbackEl, info.output, true);
    } else {
      feedbackEl.hidden = true;
    }
    state.deployInfo = info;
    if (info.root) $("#deploy-root").textContent = info.root;
  } catch (error) {
    feedback(feedbackEl, String(error), true);
  }
  renderItems();
  renderPlaced();
  syncControls();
}

async function preview(kind: "deploy" | "undeploy"): Promise<void> {
  const bridge = api();
  const token = state.deployToken;
  if (!bridge || !token || state.running || state.pendingPreview) return;
  const names = [...state.deploySelected];
  if (kind === "deploy" && !names.length) return;
  state.previewOpener = document.activeElement as HTMLElement | null;
  const label = kind === "deploy" ? "部署到專案" : "收回專案裡的部署";
  const result = await perform(`${label}前預覽`, () => kind === "deploy"
    ? bridge.preview_deploy(token, names) : bridge.preview_undeploy(token));
  if (!result || !("changes" in result)) return;
  const shown = result as ChangePreview;
  renderChanges(shown);
  if (shown.code !== 0) return;
  if (!shown.needs_confirmation) {
    outputState.textContent = kind === "deploy" ? "都已在專案裡，不需要變更" : "沒有要收回的東西";
    return;
  }
  const count = shown.changes.filter(change => change.operation !== "skip").length;
  armPreview({ kind, token: shown.token, scope: state.selectedTool, label: `${label}（${count} 項變更）` },
    kind === "deploy"
      ? "只寫進這個專案，不動這台電腦的全域設定。標示略過的是專案裡已有不同內容的檔案，不會被覆蓋。"
      : "只刪除部署後沒被改過的檔案；改過的會保留並列出。");
}

$("#deploy-open").addEventListener("click", () => { showView("deploy"); void refreshDeploy(); });
$("#deploy-back").addEventListener("click", goBack);
$("#deploy-select").addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview) return;
  setBusy(true, "選擇專案");
  try {
    const selection = await bridge.select_deploy_project();
    if (selection.code !== 0) feedback(feedbackEl, selection.output, true);
    else if (!selection.cancelled && selection.project_token) {
      state.deployToken = selection.project_token;
      $("#deploy-root").textContent = selection.root ?? "";
    }
  } catch (error) { feedback(feedbackEl, String(error), true); }
  finally { setBusy(false); }
  await refreshDeploy();
});
$("#deploy-clear").addEventListener("click", () => {
  state.deploySelected.clear();
  renderItems();
  syncControls();
});
$("#deploy-preview").addEventListener("click", () => { void preview("deploy"); });
$("#deploy-remove").addEventListener("click", () => { void preview("undeploy"); });
