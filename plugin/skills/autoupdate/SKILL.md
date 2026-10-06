---
name: autoupdate
description: 查這台機器每晚排程(自動更新 acg 與各家 AI CLI、上傳記憶)的設定與昨晚結果
disable-model-invocation: true
allowed-tools: Bash(acg autoupdate status), Bash(ai-config autoupdate status), Bash(acg memory autopush status), Bash(ai-config memory autopush status)
---

# autoupdate:查每天自動更新

執行 `acg autoupdate status` 和 `acg memory autopush status`,把兩者合起來回答「昨晚的排程
跑得怎樣」:有沒有啟用、每晚幾點、每個工具從哪版到哪版、記憶有沒有上傳(沒傳的原因,
例如十二小時內推過或沒有變更,都是正常的;上次上傳失敗會另外講)。

要看完整的過程或查失敗原因,每晚的完整輸出在:Linux 是
`journalctl --user -u acg-autopush.service`,Windows 和 macOS 是 `~/.local/state/acg/nightly.log`
(Windows 只留最後一晚)。上傳記憶那段只列出檔名和增減行數,不印內容;要看改了什麼,照它印的
`git -C … show HEAD` 去資料庫查。

自動更新沒有自己的排程,跟「每天自動上傳記憶」共用同一個每晚排程和時段:先依序叫
`claude update`、`codex update`、`agy update`,最後是 `acg update`(它會順便更新 /acg
plugin),然後才用剛裝好的 acg 上傳記憶。所以白天發布的修正,當晚上傳就會用到。這台沒裝的
工具直接略過;一個失敗不會擋住其他的,也不會擋住上傳。

有裝 herdr 的機器也會更新 herdr(acg 從不安裝它)。但 `herdr update` 會重開 server、中斷窗格裡的
agent,所以只要有 herdr session 在跑就不更新;這時若真的有新版,會標 ⚠ 請使用者方便時自己跑
`herdr update`。用 Homebrew、mise、Nix 裝的 herdr 交給那些套件管理員。每台機器各自設定,只影響這台。

兩個開關互相獨立:開自動更新不會順便開自動上傳,反之亦然;只要有一個開著,排程就在。
改時間用 `acg memory autopush enable <時>` 或桌面程式的「每晚排程的時間」,兩者一起移動。

用 npm 全域安裝的 Codex 不會自動更新,因為那個目錄通常要 sudo。status 會列出這種情況,
建議改用官方獨立安裝版(`codex` 解析到 `<CODEX_HOME>/packages/standalone/` 底下的那種,
例如 `~/.codex/packages/standalone/`;多帳號的機器可能在 `~/.codex-xxx/` 底下)。

每個工具更新完,會刪掉它自我更新時留在旁邊的舊執行檔(`agy.<n>.old`、Windows 上的
`claude.exe.old.<n>`,一個約 200 MB)。Windows 上還被執行中 session 占用的舊檔刪不掉,
會顯示「N 個舊執行檔使用中,下次再清」;這不是錯誤,關掉那些舊 session 後下一次就會清掉。

Codex 自己不清舊版:獨立安裝版與 app-server daemon 每升一版就在
`~/.codex*/packages/*/releases/` 多留一份(一份約 400 MB,曾經累積到 14 GB)。codex 那一步
會刪掉 `current` 指向以外、也沒有執行中 codex 在用的版本;npm 裝的 codex 也會清 daemon 的舊版。

有工具標 ✗ 時,那行後面就是它自己的錯誤訊息,照著講給使用者聽。標 ⚠ 的沒有失敗,但安裝方式
讓它沒辦法自動更新(例如 npm 裝的 codex),要使用者處理,一樣講清楚該怎麼做。處理完可以用
`acg autoupdate run` 立即重跑;全部成功且沒有 ⚠ 就會清掉紀錄,新 session 開頭也不再提醒。

沒啟用就照實說,啟用方式是 `acg autoupdate enable [時]`。不要替使用者決定時間,也不要
自己去啟用或執行 `run`,那會真的下載並替換這台的工具。

引數:`$ARGUMENTS`
