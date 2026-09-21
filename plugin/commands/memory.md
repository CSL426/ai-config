---
description: 看共用記憶的狀態、索引漂移與疑似憑證的筆記
disable-model-invocation: true
allowed-tools: Bash(acg memory status), Bash(ai-config memory status)
---

!`acg memory status`

把結果整理給使用者。重點看三件事:

- **共用連結與各工具入口**是否都已安裝。沒裝的話開新會話讀不到記憶。
- **Codex 與 Antigravity 有沒有裝 remember**。沒裝的話它們的工作不會進專案日誌;
  Codex 裝了還要在 Codex 裡輸入 `/hooks` 看過一次才算信任。缺的用 `acg memory enable codex`
  或 `acg memory enable agy` 補,不要在這裡代跑。
- **索引漂移**。有筆記沒被索引連到,表示下次開會話不會讀到它;索引連到不存在的檔案,表示連結該清掉。索引的摘要是人寫的無法自動重建,所以只能回報,要由人決定怎麼補。
- **疑似含有憑證的筆記**。上傳會被擋下。把檔案列給使用者,讓他自己看內容判斷,不要替他決定那是不是真的憑證。本機日誌不同步,不在掃描範圍。
- **還沒同步的專案數**。日誌預設只留在本機那台,要 adopt 過才會跟著共用記憶走。
  看到「另外 N 個專案的日誌還沒同步」就告訴使用者可以用
  `acg memory adopt --scan` 一次處理,但**不要代跑**——那會搬動好幾個專案的檔案。

若有未保存的記憶變更,提醒可以用 `acg memory push` 保存。
