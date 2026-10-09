---
name: hooks
description: 列出這台的 Claude Code hook,或把一個 Claude Code hook 分享給 Codex 與 Antigravity(共用 hook)
argument-hint: '[list | share [<編號> --name <名稱> [--to both|codex|agy]] | unshare <名稱>]'
disable-model-invocation: true
allowed-tools: Bash(acg hooks:*), Bash(ai-config hooks:*), Bash(acg apply:*), Bash(ai-config apply:*)
---

# hooks:本機 hook 與共用 hook

沒有引數就執行 `acg hooks list`,分兩段回報:

- **本機 hook**(commit-style、memory-entry、handoff-reminder):指向這台的執行檔,不同步。
  開關用 `acg hooks enable|disable <名稱>`。
- **共用 hook**:在 Claude Code 寫一次,`apply` 時也寫進 Codex(每個 CODEX_HOME 的
  `hooks.json`)與 Antigravity(`~/.gemini/config/hooks.json` 的 `acg-<名稱>`)。列表會說
  哪個工具收不到、為什麼。

## 分享

1. `acg hooks share` 列出 Claude Code 自己寫的 command hook,每個有編號。
2. 跟使用者確認要分享哪個、取什麼名字(小寫英數與 `. _ -`),再執行
   `acg hooks share <編號> --name <名稱>`;只給其中一個工具時加 `--to codex` 或 `--to agy`。
3. 執行 `acg apply` 寫進各工具,`acg push` 同步到其他機器。

寫 hook 時以 Claude Code 為準:Codex 吃同一種格式。Antigravity 由 acg 轉接,但它**不給
指令的輸出**,需要知道結果的 hook 要能改用 `cwd` 判斷;它只接 `Bash` 的 matcher 與
PreToolUse、PostToolUse、SessionStart。Codex 沒有 `Stop`。

## Codex 要信任

Codex 對新的或改過的 hook,要使用者在 Codex 裡打 `/hooks` 檢視並信任才會跑,每個
CODEX_HOME(例如不同帳號)各一次。列表會指出哪個 home 還沒信任。**不要替使用者去改
config.toml 的信任紀錄**,這是 Codex 的安全關卡,告訴使用者怎麼做就好。

## 取消分享

`acg hooks unshare <名稱>` 把它放回 Claude Code 自己的 settings.json,再 `acg apply`
從另外兩個工具移除。

引數:`$ARGUMENTS`
