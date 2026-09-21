/** Every element the app talks to, resolved once.
 *
 * `$` throws when an id is missing rather than handing back null, so a
 * renamed element fails at load with the selector in the message instead
 * of somewhere later as "cannot read property of null".
 */

import type { AcgCommand } from "./bridge";

export const REMEMBER_HOSTS = ["codex", "agy"] as const;

export const COMMAND_LABELS: Record<AcgCommand, string> = {
  status: "檢查狀態", apply: "套用設定", pull: "下載更新", push: "上傳變更",
};
export const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`missing element: ${selector}`);
  return element;
};
export const versionEl = $("#version");
export const repoEl = $("#repo");
export const providerEl = $("#provider");
export const settingsBox = $("#settings");
export const settingsClose = $<HTMLButtonElement>("#settings-close");
export const settingsFeedback = $("#settings-feedback");
export const settingsRetry = $<HTMLButtonElement>("#settings-retry");
export const settingsSwitch = $("#settings-switch");
export const settingsSwitchForm = $("#settings-switch-form");
export const githubGroup = $("#settings-github");
export const githubState = $("#github-state");
export const githubAccounts = $("#github-accounts");
export const githubActions = $("#github-actions");
export const githubLoginBtn = $<HTMLButtonElement>("#github-login");
export const githubFallback = $("#github-fallback");
export const githubCode = $("#github-code");
export const githubCodeValue = $("#github-code-value");
export const githubCodeCopy = $<HTMLButtonElement>("#github-code-copy");
export const githubCodeHint = $("#github-code-hint");
export const toolTabs = Array.from(document.querySelectorAll<HTMLButtonElement>(".scope-tab"));
export const toolRows = Array.from(document.querySelectorAll<HTMLElement>(".tool-row"));
export const heroMark = $("#hero-mark");
export const heroTitle = $("#hero-title");
export const heroSub = $("#hero-sub");
export const confirmBox = $("#confirm");
export const confirmYes = $<HTMLButtonElement>("#confirm-yes");
export const outputTitle = $("#output-title");
export const outputState = $("#output-state");
export const outputBody = $("#output-body");
export const outputCopy = $<HTMLButtonElement>("#output-copy");
export const copyFallback = $<HTMLTextAreaElement>("#copy-fallback");
export const skillList = $("#skill-list");
export const skillSearch = $<HTMLInputElement>("#skill-search");
export const skillFilter = $<HTMLSelectElement>("#skill-filter");
export const skillResult = $("#skill-result");
export const skillRetry = $<HTMLButtonElement>("#skill-retry");
export const skillSource = $("#skill-source");
export const packageMessage = $<HTMLTextAreaElement>("#package-message");
export const packageCopy = $<HTMLButtonElement>("#package-copy");
export const pluginInstall = $<HTMLTextAreaElement>("#plugin-install");
export const pluginCopy = $<HTMLButtonElement>("#plugin-copy");
export const updateBtn = $<HTMLButtonElement>("#update-check");
export const appNotice = $("#app-notice");
export const operationStatus = $("#operation-status");
export const setupBox = $("#setup");
export const setupGitPanel = $("#setup-git-panel");
export const setupGdrivePanel = $("#setup-gdrive-panel");
