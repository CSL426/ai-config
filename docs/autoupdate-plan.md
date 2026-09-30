# 每天自動更新 acg 與各家 AI CLI

狀態:**Done**(2026-09-30 使用者確認)。

## 目的

每台機器每天固定時間把 acg、Claude Code、Codex、Antigravity 更新到最新版,
不必有人記得去跑。2026-09-30 發現兩台機器各留著一份過期的 Codex
(0.147.0、0.77.0),平常用不到,但 PATH 一變就會默默退回舊版。

## 介面

`acg autoupdate status | enable [時] | disable | run`。1.0.102 時它有自己的排程
(05:30);2026-09-30 使用者決定和 `memory autopush` 綁在一起(1.0.103):

- 只有一個每晚排程,就是 autopush 原本那一個(unit `acg-autopush`、工作
  「acg memory autopush」),指令改成 `acg __nightly --if-stale 12`。
- 開了自動更新就先更新,再用安裝位置的啟動器(也就是剛裝好的新版)跑
  `memory push --if-stale`。理由:一台機器的日誌被佔位符誤判擋了一晚,修正白天
  就發布了,只是還沒裝;綁在一起的話當晚就生效。
- 兩個開關各自獨立(`~/.local/state/acg/nightly.json`):上傳記憶和替換
  執行檔是兩種不同的同意,開一個不會順便開另一個;有一個開著排程就在。
  沒有設定檔的舊排程照舊解讀成「只上傳」,所以沒重新 enable 的機器行為不變。
- 時段就是 autopush 在共用時間表裡的時段;單次上限從 15 分鐘放寬到 60 分鐘。

更新的部分(也可以用 `acg autoupdate run` 手動跑)依序:

| 項目 | 指令 | 只在這台有裝時 |
| --- | --- | --- |
| Claude Code | `claude update` | `claude` 存在 |
| Codex | `codex update`(官方獨立安裝版) | `codex` 不在 node_modules 底下 |
| Antigravity | `agy update` | `agy` 存在 |
| acg | `acg update`(已是最新就跳過) | 一律,排最後 |

acg 排最後:它更新時會透過 `claude` 順便更新 /acg plugin,那時 claude
應該已經是新的。

一項失敗不影響其他項。每項的結果(版本前後、成功或原因)記在本機
狀態檔,沿用自動上傳失敗的做法:`autoupdate status` 列出、Claude 新
session 開頭提示一次、GUI 自動化頁面顯示。

## 要注意的

- 更新進行中可能有 session 正在用舊版。acg 已是 onedir 版本目錄,
  Claude Code 與 Codex 的官方安裝也都是版本目錄加連結,不會覆寫執行中的
  檔案。Antigravity 是單一執行檔,`agy update` 先把舊檔改名為
  `agy.<數字>.old` 再把新檔改名就位(2026-09-30 在 x86_64 工作站 以 strace
  實測,1.2.13→1.2.14,當時另有一個 agy 在執行,沒受影響)。
- Antigravity 自己也有更新器:每次啟動在背景檢查(15 分鐘內不重查),
  keepalive 一天四次啟動它就會觸發。但 2026-09-30 12:00 那次沒有升上
  1.2.14,五分鐘後手動 `agy update` 才升上,所以排程裡仍要明確跑一次。
- 不是官方獨立安裝版的 Codex(npm 全域)不自動更新,只在 status 提醒,
  免得碰到要 sudo 的系統目錄。
- `acg update` 本身會更新 /acg plugin,不必另外處理。

## 各介面

CLI、`--help`、`ai_config/guide.py`、GUI 自動化頁面(開關與時間,與
自動上傳同一列版面)、`plugin/skills/` 的子指令說明、`plugin.json` 版號,
以及三個平台的排程測試。

## 驗證

- 2026-09-30 x86_64 工作站 從原始碼實跑 `autoupdate run`:claude 2.1.285、codex
  0.159.2、agy 1.2.14 皆已是最新,acg 1.0.100 → 1.0.101,共 28 秒,
  `status` 正確列出各工具結果。

## 未知

- Antigravity 內建更新器 12:00 那次為何沒升級(當時新版是否已發布)。
- Windows 上 `claude update` 在排程(無主控台)下**有新版要裝**時的行為;
  已是最新時確認不會卡住。等 claude 真的出新版再驗。

(已驗證:Windows 上 `agy update` 1.2.13→1.2.14 成功,執行中的舊 exe 被改名為
`agy.exe.<n>.old`,刪不掉的會在狀態裡標「使用中,下次再清」。)
