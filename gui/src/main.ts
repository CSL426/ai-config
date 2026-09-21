import "./style.css";
import {
  api,
  toolLabel,
  feedback,
  onSync,
  syncControls,
  setBusy,
  showView,
  goBack,
  openOutput,
  presentResult,
  perform,
  firstErrorLine,
  copyText,
  renderOutput,
  armPreview,
  closePreview,
  lastOutput,
  installMessage,
} from "./shell";
import {
  refreshMemory,
  openApply,
  setRequestPush,
} from "./memory";
import { closeActiveSelect, initializeSelects } from "./select";

import type { RememberHost, AcgCommand, GithubAccess, PushScope, RunResult, SkillEntry, ToolScope,
} from "./bridge";

import {
  $,
  COMMAND_LABELS,
  REMEMBER_HOSTS,
  appNotice,
  confirmBox,
  confirmYes,
  githubAccounts,
  githubActions,
  githubCode,
  githubCodeCopy,
  githubCodeHint,
  githubCodeValue,
  githubFallback,
  githubGroup,
  githubLoginBtn,
  githubState,
  heroMark,
  heroSub,
  heroTitle,
  outputState,
  packageCopy,
  packageMessage,
  pluginCopy,
  pluginInstall,
  providerEl,
  repoEl,
  settingsBox,
  settingsClose,
  settingsFeedback,
  settingsRetry,
  settingsSwitch,
  settingsSwitchForm,
  setupGdrivePanel,
  setupGitPanel,
  skillFilter,
  skillList,
  skillResult,
  skillRetry,
  skillSearch,
  skillSource,
  toolRows,
  toolTabs,
  updateBtn,
  versionEl,
} from "./dom";

import { state } from "./state";


function setHero(mark: "ok" | "pending" | "fail" | "none", title: string, sub: string): void {
  heroMark.className = mark === "none" ? "hero-mark" : `hero-mark is-${mark}`;
  heroTitle.textContent = title;
  heroSub.textContent = sub;
}

function invalidateToolStates(label = "需重新檢查"): void {
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

function stopHeroClock(): void {
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

function markStale(reason: string): void {
  if (!lastStatus) { invalidateToolStates(); return; }
  lastStatus.stale = reason;
  renderHeroClock();
}

function updateStatus(result: RunResult, tool: string): void {
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

async function runCommand(cmd: AcgCommand): Promise<void> {
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

async function previewPush(scope: PushScope = state.selectedTool): Promise<void> {
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

function visibleSkills(): SkillEntry[] {
  const query = skillSearch.value.trim().toLocaleLowerCase();
  return state.skills.filter(
    (skill) =>
      skill.name.toLocaleLowerCase().includes(query) &&
      matchesSkillFilter(skill, skillFilter.value),
  );
}

function renderSkills(): void {
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

async function loadSkills(): Promise<void> {
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
      markStale("技能已變更");
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
      markStale("技能已變更");
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
  if (result) feedback($("#memory-feedback"), result.output, result.code !== 0);
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
  if (result) feedback($("#memory-feedback"), result.output, result.code !== 0);
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
      feedback($("#memory-feedback"), result.output, true);
      return;
    }
    feedback($("#memory-feedback"), wanted ? "已排定錨定用量視窗" : "已取消錨定");
    await refreshMemory();
  } catch (error) {
    toggle.checked = !wanted;
    feedback($("#memory-feedback"), `無法更新排程：${String(error)}`, true);
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
    feedback($("#memory-feedback"), result.output, result.code !== 0);
    if (result.code === 0) await refreshMemory();
  } catch (error) {
    feedback($("#memory-feedback"), `無法更新排程：${String(error)}`, true);
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
      feedback($("#memory-feedback"), result.output, true);
      return;
    }
    feedback($("#memory-feedback"), wanted ? "已排定每天自動上傳" : "已取消自動上傳");
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
      feedback($("#memory-feedback"), result.output, true);
      return;
    }
    feedback($("#memory-feedback"), `這台改到每天 ${clock}`);
    await refreshMemory();
  } finally {
    button.disabled = false;
  }
});

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

function field(panel: HTMLElement, name: string): HTMLInputElement {
  const input = panel.querySelector<HTMLInputElement>(`[data-field="${name}"]`);
  if (!input) throw new Error(`missing field: ${name}`);
  return input;
}

function bindSpace(panel: HTMLElement): void {
  const sync = (): void => {
    const folder = panel.querySelector<HTMLElement>('[data-field-row="gdrive-folder"]');
    if (folder) folder.hidden = panel.querySelector<HTMLInputElement>('[data-field="gdrive-space"]:checked')?.value === "hidden";
  };
  for (const radio of panel.querySelectorAll<HTMLInputElement>('[data-field="gdrive-space"]')) {
    radio.addEventListener("change", sync);
  }
  sync();
}
bindSpace(setupGdrivePanel);
for (const radio of document.querySelectorAll<HTMLInputElement>('input[name="setup-provider"]')) {
  radio.addEventListener("change", () => {
    if (!radio.checked) return;
    setupGitPanel.hidden = radio.value !== "git";
    setupGdrivePanel.hidden = radio.value !== "gdrive";
  });
}

async function submitSetup(provider: string, panel: HTMLElement, switching = false): Promise<void> {
  const bridge = api();
  if (!bridge || state.running || state.pendingPreview) return;
  if (provider === "git" && !field(panel, "repo-url").value.trim()) {
    const input = field(panel, "repo-url");
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", switching ? "settings-feedback" : "app-notice");
    feedback(switching ? settingsFeedback : appNotice, "請先填入儲存庫 Git URL。", true);
    input.focus();
    return;
  }
  panel.querySelector('[aria-invalid="true"]')?.removeAttribute("aria-invalid");
  const dataDir = field(panel, "data-dir").value;
  const repoUrl = provider === "git" ? field(panel, "repo-url").value : "";
  // 私有 HTTPS 儲存庫需要綁一個 gh 已登入的帳號;切換表單是複製來的,欄位可能不存在
  const account = provider === "git"
    ? (panel.querySelector<HTMLInputElement>('[data-field="account"]')?.value ?? "")
    : "";
  const folder = provider === "gdrive" ? field(panel, "gdrive-folder").value : "";
  const space = panel.querySelector<HTMLInputElement>('[data-field="gdrive-space"]:checked')?.value ?? "visible";
  if (switching) showSettings(false);
  const task = () =>
    provider === "git"
      ? bridge.setup_repo(repoUrl, dataDir, account)
      : bridge.setup_gdrive(dataDir, folder, space);
  const result = await perform(switching ? "切換同步方式" : "首次設定", task);
  if (result?.code === 0) {
    // 後端工具路徑於啟動時載入；完成 setup 後不可使用舊程序繼續同步。
    state.restartRequired = true;
    feedback(appNotice, "設定完成。請關閉並重新開啟視窗，載入新的連線設定。");
    syncControls();
  }
}
$("#setup-go").addEventListener("click", () => { void submitSetup("git", setupGitPanel); });
$("#setup-gdrive-go").addEventListener("click", () => { void submitSetup("gdrive", setupGdrivePanel); });

async function loadInfo(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  try {
    const info = await bridge.get_info();
    state.connected = true;
    state.configured = info.configured;
    versionEl.textContent = info.build_commit
      ? `v${info.version} · ${info.build_commit.slice(0, 8)}`
      : `v${info.version}`;
    versionEl.title = info.build_commit || "";
    providerEl.textContent = state.configured ? providerLabel(info.provider) : "尚未設定同步方式";
    providerEl.dataset.provider = state.configured ? info.provider : "none";
    repoEl.textContent = `本機設定位置：${info.repo}`;
    repoEl.title = info.repo;
    field(setupGitPanel, "data-dir").value = info.repo;
    field(setupGdrivePanel, "data-dir").value = info.repo;
    feedback(appNotice, info.config_error ? `設定檔有問題：${info.config_error}` : "", Boolean(info.config_error));
    $("#info-retry").hidden = true;
    showView(state.currentView, false);
  } catch (error) {
    state.connected = false;
    feedback(appNotice, `無法讀取連線資訊：${String(error)}`, true);
    $("#info-retry").hidden = false;
  } finally {
    syncControls();
  }
}
$("#info-retry").addEventListener("click", () => { void boot(); });

// 後端在 pull／push 被私有儲存庫拒絕時會印出這句;看到就給一顆直接去登入的按鈕
const REFUSED_MARKER = "遠端拒絕存取";

function offerLogin(output: string): void {
  if (!output.includes(REFUSED_MARKER)) return;
  feedback(appNotice, "遠端拒絕存取：這台還沒有能讀取資料儲存庫的帳號。", true);
  const button = document.createElement("button");
  button.className = "btn btn-primary";
  button.textContent = "前往設定登入";
  button.addEventListener("click", () => { showSettings(true); });
  appNotice.append(document.createTextNode(" "), button);
}

function providerLabel(provider: string): string {
  return provider === "gdrive" ? "Google Drive" : "私人 Git 儲存庫";
}

// ── GitHub 上傳權限 ─────────────────────

let githubPollTimer: number | null = null;

function stopGithubPolling(): void {
  if (githubPollTimer !== null) {
    window.clearTimeout(githubPollTimer);
    githubPollTimer = null;
  }
}

function resetGithubCode(): void {
  stopGithubPolling();
  githubCode.hidden = true;
  githubCodeValue.textContent = "————";
  githubCodeHint.textContent = "";
  githubLoginBtn.disabled = false;
  githubLoginBtn.textContent = "用瀏覽器登入 GitHub";
}

async function loadGithubAccess(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  resetGithubCode();
  githubGroup.hidden = false;
  githubState.textContent = "檢查中…";
  githubAccounts.hidden = true;
  githubAccounts.replaceChildren();
  githubActions.hidden = true;

  let access: GithubAccess;
  try {
    access = await bridge.github_access();
  } catch {
    githubGroup.hidden = true;
    return;
  }

  // 遠端不是 GitHub 就沒什麼好說的,整段收起來
  if (!access.repository) {
    githubGroup.hidden = true;
    return;
  }

  githubState.textContent = access.lines.join(" ");
  if (access.can_push === true) return;

  // 已經登入過的其他帳號通常就是解法,直接讓人一鍵切過去
  const others = access.accounts.filter(
    (name: string) => name !== access.account,
  );
  if (others.length > 0) {
    for (const name of others) {
      const button = document.createElement("button");
      button.className = "btn btn-ghost";
      button.textContent = `改用 ${name}`;
      button.addEventListener("click", () => void useGithubAccount(name));
      githubAccounts.append(button);
    }
    githubAccounts.hidden = false;
  }
  githubActions.hidden = !access.installed || !access.device_login;
  // 沒有內建瀏覽器登入的版本:給一顆真的按鈕,替使用者開終端機跑 gh 的登入
  githubFallback.hidden = !access.installed || access.device_login;
}

async function terminalGithubLogin(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  githubState.textContent = "正在開啟終端機…";
  const result = await bridge.github_terminal_login();
  githubState.textContent = result.output;
}

async function useGithubAccount(account: string): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  githubState.textContent = `正在切換到 ${account}…`;
  const result = await bridge.github_use_account(account);
  githubState.textContent = result.output;
  if (result.code === 0) void loadGithubAccess();
}

async function startGithubLogin(): Promise<void> {
  const bridge = api();
  if (!bridge) return;
  githubLoginBtn.disabled = true;
  githubLoginBtn.textContent = "準備中…";

  const start = await bridge.github_start_login();
  if (start.code !== 0 || !start.device_code) {
    githubState.textContent = start.output || "無法開始登入";
    resetGithubCode();
    return;
  }

  githubCodeValue.textContent = start.user_code ?? "";
  githubCodeHint.textContent = `已開啟 ${start.verification_uri}，完成後這裡會自動更新`;
  githubCode.hidden = false;
  githubLoginBtn.textContent = "等待瀏覽器確認…";

  const interval = Math.max(start.interval ?? 5, 1);
  const deadline = Date.now() + 15 * 60 * 1000;

  const poll = async (): Promise<void> => {
    if (Date.now() > deadline) {
      githubState.textContent = "登入逾時，請再試一次";
      resetGithubCode();
      return;
    }
    const result = await bridge.github_poll_login(start.device_code!, interval);
    if (result.status === "pending") {
      githubPollTimer = window.setTimeout(() => void poll(), interval * 1000);
      return;
    }
    resetGithubCode();
    githubState.textContent = result.output;
    if (result.status === "done") void loadGithubAccess();
  };

  githubPollTimer = window.setTimeout(() => void poll(), interval * 1000);
}

githubLoginBtn.addEventListener("click", () => void startGithubLogin());
$("#github-terminal-login").addEventListener("click", () => void terminalGithubLogin());
$("#github-recheck").addEventListener("click", () => void loadGithubAccess());

githubCodeCopy.addEventListener("click", async () => {
  const ok = await copyText(githubCodeValue.textContent ?? "");
  githubCodeCopy.textContent = ok ? "已複製！" : "請手動複製";
  window.setTimeout(() => {
    githubCodeCopy.textContent = "複製";
  }, 2000);
});

async function loadSettings(): Promise<void> {
  const bridge = api();
  if (!bridge || state.settingsLoading) return;
  state.settingsLoading = true;
  state.settingsInfo = null;
  settingsSwitch.hidden = true;
  settingsRetry.hidden = true;
  feedback(settingsFeedback, "讀取設定中…");
  syncControls();
  try {
    const info = await bridge.settings_info();
    state.settingsInfo = info;
    for (const radio of settingsBox.querySelectorAll<HTMLInputElement>('input[name="settings-provider"]')) {
      radio.checked = radio.value === info.provider;
      const badge = radio.closest(".provider-choice")?.querySelector<HTMLElement>(".provider-choice-current");
      if (badge) badge.hidden = !radio.checked;
    }
    $("#settings-row-account").hidden = info.provider !== "gdrive";
    $("#settings-row-folder").hidden = info.provider !== "gdrive";
    $("#settings-row-remote").hidden = info.provider === "gdrive" || !info.remote_url;
    $("#settings-account").textContent = info.signed_in ? "已授權" : "尚未登入";
    $("#settings-folder").textContent = info.gdrive_space === "hidden"
      ? "隱藏的應用程式空間" : `我的雲端硬碟 / ${info.gdrive_folder}`;
    $("#settings-folder").title = $("#settings-folder").textContent ?? "";
    $("#settings-folder-open").hidden = !info.gdrive_folder_url;
    $("#settings-folder-open").dataset.url = info.gdrive_folder_url;
    $("#settings-remote").textContent = info.remote_url || "—";
    $("#settings-repo").textContent = info.repo;
    feedback(settingsFeedback, "");
    // 只有 git provider 才談得上 GitHub 推送權限
    if (info.provider === "gdrive") {
      githubGroup.hidden = true;
      resetGithubCode();
    } else {
      void loadGithubAccess();
    }
  } catch (error) {
    feedback(settingsFeedback, `無法讀取設定：${String(error)}`, true);
    settingsRetry.hidden = false;
  } finally {
    state.settingsLoading = false;
    syncControls();
  }
}

function showSettings(show: boolean): void {
  settingsBox.hidden = !show;
  // 關閉時停止輪詢並清掉畫面:否則離開後還在打 GitHub 的 API,
  // 而且下次打開會看到一組早就失效的舊驗證碼
  if (!show) resetGithubCode();
  for (const child of Array.from($(".app").children)) {
    if (child instanceof HTMLElement && child !== settingsBox) child.inert = show;
  }
  if (show) {
    state.settingsOpener = document.activeElement as HTMLElement | null;
    settingsClose.focus();
    void loadSettings();
  } else {
    state.settingsOpener?.focus();
    state.settingsOpener = null;
  }
}
$("#settings-open").addEventListener("click", () => { showSettings(true); });
settingsClose.addEventListener("click", () => { showSettings(false); });
settingsRetry.addEventListener("click", () => { void loadSettings(); });
settingsBox.addEventListener("click", event => {
  if (event.target === settingsBox) showSettings(false);
});
document.addEventListener("keydown", event => {
  if (event.defaultPrevented || event.isComposing) return;
  if (event.key === "Escape") {
    event.preventDefault();
    if (event.repeat || closeActiveSelect()) return;
    if (!settingsBox.hidden) showSettings(false);
    else goBack();
    return;
  }
  if (settingsBox.hidden) return;
  if (event.key === "Tab") {
    const focusable = Array.from(settingsBox.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
    )).filter(element => element.getClientRects().length > 0);
    const first = focusable[0];
    const last = focusable.at(-1);
    const outside = !settingsBox.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || outside)) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && (document.activeElement === last || outside)) {
      event.preventDefault();
      first?.focus();
    }
  }
});
$("#settings-open-dir").addEventListener("click", async () => {
  const bridge = api();
  if (!bridge) return;
  const result = await perform("開啟資料夾", () => bridge.open_data_dir(), false);
  if (result) feedback(settingsFeedback, result.code === 0 ? "已開啟資料夾。" : firstErrorLine(result.output), result.code !== 0);
});
$("#settings-folder-open").addEventListener("click", () => {
  const url = $("#settings-folder-open").dataset.url;
  if (url) window.open(url, "_blank", "noopener,noreferrer");
});
$("#settings-relogin").addEventListener("click", async () => {
  const bridge = api();
  if (!bridge || state.settingsInfo?.provider !== "gdrive") return;
  feedback(settingsFeedback, "請在瀏覽器完成 Google 登入。儲存位置保持不變。");
  const result = await perform("重新登入 Google", () => bridge.relogin_gdrive(), false);
  if (!result) return;
  if (result.code === 0) await loadSettings();
  feedback(settingsFeedback, result.code === 0 ? "已重新登入，儲存位置保持不變。" : firstErrorLine(result.output), result.code !== 0);
  if (settingsBox.hidden) openOutput();
});

function offerProviderSwitch(provider: string): void {
  const info = state.settingsInfo;
  if (!info) return;
  settingsSwitch.hidden = false;
  feedback(settingsFeedback, "");
  $("#settings-switch-note").textContent = `切換到${providerLabel(provider)}需要設定新的連線。既有資料不會刪除，兩邊的資料也不會自動搬移。`;
  const source = provider === "gdrive" ? setupGdrivePanel : setupGitPanel;
  const panel = source.cloneNode(true) as HTMLElement;
  panel.hidden = false;
  panel.removeAttribute("id");
  for (const node of panel.querySelectorAll("[id]")) node.removeAttribute("id");
  // 複製表單的 radio 必須獨立分組，避免改到仍存在的首次設定表單。
  for (const radio of panel.querySelectorAll<HTMLInputElement>('input[type="radio"]')) {
    radio.name = `switch-${radio.name}`;
    if (radio.dataset.field === "gdrive-space") radio.checked = radio.value === info.gdrive_space;
  }
  field(panel, "data-dir").value = info.repo;
  if (provider === "gdrive") field(panel, "gdrive-folder").value = info.gdrive_folder || "ai-config";
  settingsSwitchForm.replaceChildren(panel);
  bindSpace(panel);
  const go = panel.querySelector<HTMLButtonElement>(".btn-primary")!;
  go.addEventListener("click", () => { void submitSetup(provider, panel, true); });
  syncControls();
  settingsSwitch.scrollIntoView({ block: "nearest" });
}
for (const radio of settingsBox.querySelectorAll<HTMLInputElement>('input[name="settings-provider"]')) {
  radio.addEventListener("change", () => {
    if (!radio.checked) return;
    if (radio.value === state.settingsInfo?.provider) settingsSwitch.hidden = true;
    else offerProviderSwitch(radio.value);
  });
}

$("#package-open").addEventListener("click", () => { showView("skills"); });
$("#package-back").addEventListener("click", goBack);
$("#output-toggle").addEventListener("click", openOutput);
$("#skill-output").addEventListener("click", openOutput);
$("#output-back").addEventListener("click", goBack);
$("#export-back").addEventListener("click", goBack);


async function boot(): Promise<void> {
  syncControls();
  await loadInfo();
  if (state.connected && state.configured) await loadSkills();
}
initializeSelects();
for (const button of document.querySelectorAll<HTMLElement>("[id$='-back'], #settings-close, #confirm-no")) {
  button.title = `${button.textContent?.trim()}（Esc）`;
}
if (window.pywebview) void boot();
else {
  syncControls();
  window.addEventListener("pywebviewready", () => { void boot(); }, { once: true });
  repoEl.textContent = "等待後端連線…";
  feedback(appNotice, "正在連接應用程式；請透過 acg gui 啟動此介面。");
}
