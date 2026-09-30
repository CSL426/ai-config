---
name: autoupdate
description: 查這台機器每天自動更新 acg 與各家 AI CLI 的設定與上次結果
disable-model-invocation: true
allowed-tools: Bash(acg autoupdate status), Bash(ai-config autoupdate status)
---

# autoupdate:查每天自動更新

執行 `acg autoupdate status`,把輸出整理給使用者:有沒有啟用、每天幾點、上次執行時每個
工具從哪版到哪版。

啟用後,每天在排定的時間(預設 05:30)依序叫 `claude update`、`codex update`、
`agy update`,最後是 `acg update`,它會順便更新 /acg plugin。這台沒裝的工具直接略過;
一個失敗不會擋住其他的。每台機器各自設定,只影響這台。

用 npm 全域安裝的 Codex 不會自動更新,因為那個目錄通常要 sudo。status 會列出這種情況,
建議改用官方獨立安裝版(`codex` 解析到 `~/.codex/packages/standalone/` 的那種)。

有工具標 ✗ 時,那行後面就是它自己的錯誤訊息,照著講給使用者聽。處理完可以用
`acg autoupdate run` 立即重跑;全部成功就會清掉失敗紀錄,新 session 開頭也不再提醒。

沒啟用就照實說,啟用方式是 `acg autoupdate enable [HH:MM]`。不要替使用者決定時間,也不要
自己去啟用或執行 `run`,那會真的下載並替換這台的工具。

引數:`$ARGUMENTS`
