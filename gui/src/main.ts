/** The entry point: import each area so its listeners register, then boot.
 *
 * Nothing here knows what an area does. The bare imports are load-bearing
 * — an area registers its own handlers as a side effect of being
 * imported, so dropping one silently removes its buttons.
 */

import "./style.css";
import "./status";
import "./memory";
import "./skills";
import "./connection";

import { initializeSelects } from "./select";
import { appNotice, repoEl } from "./dom";
import { state } from "./state";
import { feedback, syncControls } from "./shell";
import { loadSkills } from "./skills";
import { loadInfo, setRequestBoot } from "./connection";
import { runCommand } from "./status";

setRequestBoot(() => boot());

async function boot(): Promise<void> {
  syncControls();
  await loadInfo();
  if (state.connected && state.configured) {
    await loadSkills();
    // 打開就是想知道現在的狀況;不必先按一顆「檢查」
    await runCommand("status");
  }
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
