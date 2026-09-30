# 每天自動更新 acg 與各家 AI CLI

狀態:**Draft**。使用者確認前不改為 Done。

## 目的

每台機器每天固定時間把 acg、Claude Code、Codex、Antigravity 更新到最新版,
不必有人記得去跑。2026-09-30 發現兩台機器各留著一份過期的 Codex
(0.147.0、0.77.0),平常用不到,但 PATH 一變就會默默退回舊版。

## 介面

`acg autoupdate status | enable [時:分] | disable`,與 `memory autopush`、
`keepalive` 同一套排程機制:Linux 用 systemd user timer、macOS 用
LaunchAgent、Windows 用工作排程器;每台一個時段,多台自動錯開。預設在
autopush 之後(05:00 前後),避開 keepalive 的整點。

排程執行 `acg __autoupdate`,依序:

| 項目 | 指令 | 只在這台有裝時 |
| --- | --- | --- |
| acg | `acg update`(非互動,已是最新就跳過) | 一律 |
| Claude Code | `claude update` | `claude` 存在 |
| Codex | `codex update`(官方獨立安裝版) | `codex` 為獨立安裝版 |
| Antigravity | 待查官方更新方式 | `agy` 存在 |

一項失敗不影響其他項。每項的結果(版本前後、成功或原因)記在本機
狀態檔,沿用自動上傳失敗的做法:`autoupdate status` 列出、Claude 新
session 開頭提示一次、GUI 自動化頁面顯示。

## 要注意的

- 更新進行中可能有 session 正在用舊版。acg 已是 onedir 版本目錄,
  Claude Code 與 Codex 的官方安裝也都是版本目錄加連結,不會覆寫執行中的
  檔案;Antigravity 要查。
- 不是官方獨立安裝版的 Codex(npm 全域)不自動更新,只在 status 提醒,
  免得碰到要 sudo 的系統目錄。
- `acg update` 本身會更新 /acg plugin,不必另外處理。

## 各介面

CLI、`--help`、`ai_config/guide.py`、GUI 自動化頁面(開關與時間,與
自動上傳同一列版面)、`plugin/skills/` 的子指令說明、`plugin.json` 版號,
以及三個平台的排程測試。

## 未知

- Antigravity CLI 的官方更新方式與安裝版面。
- Windows 上 `claude update` 在排程(無主控台)下的行為。
