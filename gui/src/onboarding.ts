/** The home page's next step for a machine that is new to the data repository.
 *
 * Setup used to end on the home page with nothing saying whether to upload
 * this machine's settings or apply the saved ones. The card says which,
 * offers that one button, and lists the features each machine turns on by
 * itself. It hides once the step is done and the features are on, or the
 * user dismisses the suggestions.
 */

import type { OnboardingInfo } from "./bridge";
import { $, repoEl } from "./dom";
import { api } from "./shell";
import { state } from "./state";
import { previewPush, runCommand } from "./status";

const card = $("#next-step");
const main = $("#next-step-main");
const title = $("#next-step-title");
const text = $("#next-step-text");
const go = $<HTMLButtonElement>("#next-step-go");
const suggest = $("#next-step-suggest");
const dismiss = $<HTMLButtonElement>("#next-step-dismiss");
const FEATURES = ["memory", "autopush", "autoupdate"] as const;

let action: "push" | "apply" | null = null;

function dismissKey(): string {
  return `acg.next-step.dismissed:${repoEl.textContent ?? ""}`;
}

function dismissed(): boolean {
  try { return localStorage.getItem(dismissKey()) === "1"; } catch { return false; }
}

function render(info: OnboardingInfo): void {
  if (!info.configured) { card.hidden = true; return; }
  if (info.repo_empty) {
    action = "push";
    title.textContent = "這台是第一台";
    text.textContent = "置物櫃還是空的。按「上傳變更」把這台的規則、技能與設定存進去，"
      + "上傳前會先列出內容給你確認；之後其他電腦就能套用。";
    go.textContent = "上傳變更";
  } else if (!info.applied) {
    action = "apply";
    title.textContent = "這台還沒套用置物櫃的設定";
    text.textContent = "上面是這台和置物櫃的差異。確認後套用，會先備份這台現有的設定。"
      + "先套用再上傳，才不會把其他電腦的更新蓋回舊版。";
    go.textContent = "套用設定";
  } else {
    action = null;
  }
  main.hidden = action === null;

  const pending = FEATURES.filter((feature) => !info[feature]);
  for (const feature of FEATURES) {
    card.querySelector(`[data-feature="${feature}"]`)?.classList.toggle("is-done", info[feature]);
  }
  // 還沒做完那一步時一起列出;做完之後只剩建議,可以關掉
  suggest.hidden = pending.length === 0 || (action === null && dismissed());
  dismiss.hidden = action !== null;
  card.hidden = main.hidden && suggest.hidden;
}

export async function refreshOnboarding(): Promise<void> {
  const bridge = api();
  if (!bridge || !state.configured || typeof bridge.onboarding_info !== "function") {
    card.hidden = true;
    return;
  }
  try {
    render(await bridge.onboarding_info());
  } catch {
    card.hidden = true;  // 引導是錦上添花,查不到就不顯示
  }
}

go.addEventListener("click", () => {
  if (action === "push") void previewPush("all");
  else if (action === "apply") void runCommand("apply");
});
dismiss.addEventListener("click", () => {
  try { localStorage.setItem(dismissKey(), "1"); } catch { /* 無痕視窗存不了,這次關掉就好 */ }
  suggest.hidden = true;
  card.hidden = main.hidden;
});
for (const link of card.querySelectorAll<HTMLButtonElement>("[data-open]")) {
  link.addEventListener("click", () => document.getElementById(link.dataset.open ?? "")?.click());
}
document.addEventListener("acg:status-changed", () => { void refreshOnboarding(); });
