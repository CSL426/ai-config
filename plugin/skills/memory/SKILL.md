---
name: memory
description: 看共用記憶的狀態、索引漂移與疑似憑證的筆記
disable-model-invocation: true
allowed-tools: Bash(acg memory status), Bash(ai-config memory status)
---

# memory:看共用記憶的狀態

執行 `acg memory status`,把結果整理給使用者。重點看這幾件事:

- **共用連結與各工具入口**是否都已安裝。沒裝的話開新會話讀不到記憶。
- **Codex 與 Antigravity 有沒有裝 remember**。沒裝的話它們的工作不會進專案日誌;Codex 裝了
  還要在 Codex 裡輸入 `/hooks` 看過一次才算信任。缺的用 `acg memory enable codex` 或
  `acg memory enable agy` 補,不要在這裡代跑。
- **索引漂移**。有筆記沒被索引連到,下次開會話就讀不到它;索引連到不存在的檔案,表示
  連結該清掉。索引的摘要是人寫的,無法自動重建,只能回報,由人決定怎麼補。
- **疑似含有憑證的筆記**。上傳會被擋下。把檔案列給使用者,讓他自己看內容判斷,不要替他
  決定那是不是真的憑證。本機日誌不同步,不在掃描範圍。
- **還沒同步的專案數**,status 分兩行講:
  - 「另外 N 個專案的日誌還沒同步」:現在的 session 正在寫的日誌。有開每晚自動上傳的機器,
    排程會在上傳前自動 adopt 有 git 遠端的專案;沒有遠端的只列在排程日誌裡,要手動 adopt
    (鍵值只是目錄名稱,兩台機器上不相干的同名目錄會混在一起)。告訴使用者今晚會處理,
    或現在用 `acg memory adopt all`。
  - 「另有 M 個專案只剩舊時代的 .remember」:acg 接管日誌之前留在專案裡的資料夾,之後不會
    再變,所以排程不碰。要同步就手動 `acg memory adopt <路徑>`,不要就刪掉,讓使用者決定。
  兩種都不要代跑:adopt 會搬動專案裡的檔案。

若有未保存的記憶變更,提醒可以用 `acg memory push` 保存。

引數:`$ARGUMENTS`
