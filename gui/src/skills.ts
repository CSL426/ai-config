/** The skills list: filtering, selection, and the share/package actions.
 *
 * It asks the status area to mark itself stale after a change rather than
 * importing it, so status can move to its own file without the two
 * importing each other.
 */

import {
  $, packageCopy, packageMessage, skillFilter, skillList, skillResult,
  skillRetry, skillSearch, skillSource,
} from "./dom";
import { state } from "./state";
import {
  api, copyText, feedback, firstErrorLine, installMessage, onSync, perform,
  setBusy,
  showView, syncControls,
} from "./shell";
import type { RunResult, SkillEntry } from "./bridge";

let requestStale: (reason: string) => void = () => {};
export function setRequestStale(handler: (reason: string) => void): void {
  requestStale = handler;
}

onSync(({ blocked, configured: ready, restartRequired: restart }) => {
  const selected = state.skills.filter((skill) => state.selectedSkills.has(skill.name));
  const unavailable = blocked || !ready || state.skillsLoading || restart;
  const hasSelection = selected.length > 0;
  const canShare = hasSelection && selected.every((skill) => skill.shareable);
  const canUnshare = hasSelection && selected.every((skill) => skill.shared);

  $<HTMLButtonElement>("#skill-pick").disabled = unavailable;
  $<HTMLButtonElement>("#skill-add").disabled = unavailable || !state.skillDirectory;
  $<HTMLButtonElement>("#skill-share").disabled = unavailable || !canShare;
  $<HTMLButtonElement>("#skill-unshare").disabled = unavailable || !canUnshare;
  $<HTMLButtonElement>("#skill-package").disabled = unavailable || !hasSelection;
  $<HTMLButtonElement>("#skill-all").disabled = unavailable || visibleSkills().length === 0;
  $<HTMLButtonElement>("#skill-none").disabled = unavailable || !hasSelection;
  skillSearch.disabled = blocked || state.skillsLoading;
  skillFilter.disabled = blocked || state.skillsLoading;
  skillRetry.disabled = blocked || state.skillsLoading;

  $("#skill-selection").textContent = `已選 ${selected.length} 項 · 顯示 ${visibleSkills().length} / ${state.skills.length} 項`;
  $("#skill-share").title = !hasSelection || canShare
    ? "分享給 Codex 與 Antigravity"
    : "選取項目包含無法從 Claude Code 分享的技能";
  $("#skill-unshare").title = !hasSelection || canUnshare
    ? "收回已分享的技能"
    : "請只選擇已分享的技能";
});

function matchesSkillFilter(skill: SkillEntry, filter: string): boolean {
  if (filter === "shared") return skill.shared;
  if (filter === "unshared") return !skill.shared;
  return true;
}

export function visibleSkills(): SkillEntry[] {
  const query = skillSearch.value.trim().toLocaleLowerCase();
  return state.skills.filter(
    (skill) =>
      skill.name.toLocaleLowerCase().includes(query) &&
      matchesSkillFilter(skill, skillFilter.value),
  );
}

export function renderSkills(): void {
  skillList.replaceChildren();
  const visible = visibleSkills();
  if (!visible.length) {
    const message = document.createElement("p");
    message.className = "package-hint";
    message.textContent = state.skills.length ? "沒有符合條件的技能，請調整搜尋或篩選。" : "目前沒有可用的技能。";
    skillList.append(message);
  }
  for (const skill of visible) {
    const label = document.createElement("label");
    label.className = "skill-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = skill.name;
    checkbox.checked = state.selectedSkills.has(skill.name);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedSkills.add(skill.name);
      else state.selectedSkills.delete(skill.name);
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

export async function loadSkills(): Promise<void> {
  const bridge = api();
  if (!bridge || state.skillsLoading) return;
  state.skillsLoading = true;
  skillRetry.hidden = true;
  skillList.setAttribute("aria-busy", "true");
  skillList.textContent = "讀取技能中…";
  syncControls();
  try {
    const result = await bridge.list_skills();
    state.skills = result.skills;
    for (const name of state.selectedSkills) {
      if (!state.skills.some(skill => skill.name === name)) state.selectedSkills.delete(name);
    }
    renderSkills();
  } catch (error) {
    state.skills = [];
    state.selectedSkills.clear();
    skillList.textContent = `無法讀取技能：${String(error)}`;
    skillRetry.hidden = false;
  } finally {
    state.skillsLoading = false;
    skillList.setAttribute("aria-busy", "false");
    syncControls();
  }
}
skillSearch.addEventListener("input", renderSkills);
skillFilter.addEventListener("change", renderSkills);
$("#skill-all").addEventListener("click", () => {
  for (const skill of visibleSkills()) state.selectedSkills.add(skill.name);
  renderSkills();
});
$("#skill-none").addEventListener("click", () => {
  state.selectedSkills.clear();
  renderSkills();
});
skillRetry.addEventListener("click", () => { void loadSkills(); });

$("#skill-pick").addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview || !state.configured || state.restartRequired) return;
  state.skillDirectory = null;
  skillSource.textContent = "尚未選擇資料夾";
  feedback(skillResult, "");
  setBusy(true, "選擇技能資料夾");
  try {
    const selection = await bridge.select_skill_directory();
    if (selection.code !== 0) {
      feedback(skillResult, firstErrorLine(selection.output), true);
    } else if (!selection.cancelled && selection.path) {
      state.skillDirectory = selection.path;
      skillSource.textContent = selection.path;
    }
  } catch (error) {
    feedback(skillResult, `無法選擇資料夾：${String(error)}`, true);
  } finally {
    setBusy(false);
  }
});

$("#skill-add").addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview || !state.configured || state.restartRequired || !state.skillDirectory) return;
  const source = state.skillDirectory;
  feedback(skillResult, "");
  const result = await perform("安裝技能", async () => {
    const installed = await bridge.add_skill(source);
    if (installed.code === 0) {
      state.skillDirectory = null;
      skillSource.textContent = "尚未選擇資料夾";
      requestStale("技能已變更");
      await loadSkills();
    }
    return installed;
  }, false);
  if (!result) return;
  feedback(skillResult, result.code === 0
    ? "安裝完成，已加入資料庫與 Claude Code。要部署至其他工具，請返回並套用「獨立技能」。"
    : firstErrorLine(result.output), result.code !== 0);
});

const SKILL_ACTION_LABELS = {
  share: "分享技能",
  unshare: "取消分享",
  package: "匯出安裝檔",
} as const;

async function skillAction(action: "share" | "unshare" | "package"): Promise<void> {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview || !state.selectedSkills.size) return;
  const names = [...state.selectedSkills];
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
      requestStale("技能已變更");
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
