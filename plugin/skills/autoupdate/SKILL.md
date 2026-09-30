---
name: autoupdate
description: 查這台機器每天自動更新 acg 與各家 AI CLI 的設定與上次結果
disable-model-invocation: true
allowed-tools: Bash(acg autoupdate status), Bash(ai-config autoupdate status)
---

# autoupdate:查每天自動更新

執行 `acg autoupdate status`,把輸出整理給使用者:有沒有啟用、每晚幾點、上次執行時每個
工具從哪版到哪版。

自動更新沒有自己的排程,跟「每天自動上傳記憶」共用同一個每晚排程和時段:先依序叫
`claude update`、`codex update`、`agy update`,最後是 `acg update`(它會順便更新 /acg
plugin),然後才用剛裝好的 acg 上傳記憶。所以白天發布的修正,當晚上傳就會用到。這台沒裝的
工具直接略過;一個失敗不會擋住其他的,也不會擋住上傳。每台機器各自設定,只影響這台。

兩個開關互相獨立:開自動更新不會順便開自動上傳,反之亦然;只要有一個開著,排程就在。
改時間用 `acg memory autopush enable <時>` 或桌面程式的「每晚排程的時間」,兩者一起移動。

用 npm 全域安裝的 Codex 不會自動更新,因為那個目錄通常要 sudo。status 會列出這種情況,
建議改用官方獨立安裝版(`codex` 解析到 `~/.codex/packages/standalone/` 的那種)。

每個工具更新完,會刪掉它自我更新時留在旁邊的舊執行檔(`agy.<n>.old`、Windows 上的
`claude.exe.old.<n>`,一個約 200 MB)。Windows 上還被執行中 session 占用的舊檔刪不掉,
會顯示「N 個舊執行檔使用中,下次再清」;這不是錯誤,關掉那些舊 session 後下一次就會清掉。

有工具標 ✗ 時,那行後面就是它自己的錯誤訊息,照著講給使用者聽。處理完可以用
`acg autoupdate run` 立即重跑;全部成功就會清掉失敗紀錄,新 session 開頭也不再提醒。

沒啟用就照實說,啟用方式是 `acg autoupdate enable [時]`。不要替使用者決定時間,也不要
自己去啟用或執行 `run`,那會真的下載並替換這台的工具。

引數:`$ARGUMENTS`
