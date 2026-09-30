import { test, type Page } from "@playwright/test";

// CLI 的畫面:照 acg 真實輸出的格式與配色(console.py),路徑與名稱換成中性的
const out = (name: string) => `../docs/screenshots/${name}.png`;

type Line = string;
const header = (text: string): Line => `<b class="c">═══ ${text} ═══</b>`;
const ok = (text: string): Line => `<span class="g">✓</span> ${text}`;
const info = (text: string): Line => `<span class="b">ℹ</span> ${text}`;
const prompt = (command: string): Line => `<span class="p">~$</span> ${command}`;

async function terminal(page: Page, lines: Line[], name: string) {
  await page.setContent(`<!doctype html><meta charset="utf-8"><style>
    body { margin: 0; padding: 1.5rem; background: #f4f1ea; }
    .win { border-radius: 0.6rem; overflow: hidden; box-shadow: 0 0.4rem 1.2rem #0003; width: fit-content; }
    .bar { background: #2b2b2b; padding: 0.55rem 0.8rem; display: flex; gap: 0.4rem; }
    .bar i { width: 0.75rem; height: 0.75rem; border-radius: 50%; display: block; }
    pre { margin: 0; padding: 1rem 1.3rem 1.2rem; background: #1e1e1e; color: #d8d8d8;
      font: 0.95rem/1.55 "Maple Mono NF CN", "DejaVu Sans Mono", monospace; }
    .c { color: #56c8d8; } .g { color: #7ec07e; } .b { color: #6aa0ff; } .p { color: #c9a26b; }
    .dim { color: #8a8a8a; }
  </style><div class="win"><div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i></div><pre>${lines.join("\n")}</pre></div>`);
  await page.locator(".win").screenshot({ path: out(name) });
}

test("每天:看差異", async ({ page }) => {
  await terminal(page, [
    prompt("acg status"),
    "",
    header("Status: claude"),
    ok("No differences found"),
    "",
    header("Status: codex"),
    "  + skills/commit-helper/SKILL.md <span class=\"dim\">(only in ai-config; repo modified 2026-09-30T12:26:42+08:00)</span>",
    info("mtime is a hint only; Git checkout and copy operations can change it"),
    "",
    header("Status: agy"),
    ok("No differences found"),
    "",
    header("Shared skill mirrors"),
    ok("All 3 mirrored shared skills up to date"),
    "",
    prompt("acg apply codex"),
  ], "cli-status");
});

test("每晚:自動更新與上傳", async ({ page }) => {
  await terminal(page, [
    prompt("acg autoupdate status"),
    "",
    header("Auto-update"),
    ok("已啟用,每晚 04:10 先更新 acg、Claude Code、Codex、Antigravity,再上傳記憶(若自動上傳也開著)"),
    "  上次執行 2026-10-01 04:12",
    `    ${ok("claude:2.1.285 → 2.1.290")}`,
    `    ${ok("codex:0.159.2 已是最新")}`,
    `    ${ok("agy:1.2.14 → 1.2.15(清掉舊執行檔 200 MB)")}`,
    `    ${ok("acg:1.0.103 → 1.0.104")}`,
    "",
    prompt("acg memory autopush status"),
    info("平台:linux"),
    ok("已排定每天自動推送記憶"),
    info("上次推送:2026-10-01 04:12"),
    info("現在執行的話:記憶沒有變更"),
  ], "cli-nightly");
});
