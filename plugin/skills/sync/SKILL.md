---
name: sync
description: 把資料庫的更新拉下來,然後顯示差異
argument-hint: '[claude|codex|agy|all]'
disable-model-invocation: true
allowed-tools: Bash(acg pull:*), Bash(ai-config pull:*)
---

# sync:把資料庫的更新拉下來

執行 `acg pull [claude|codex|agy|all]`。

拉取只快轉,不會合併。若它拒絕執行,原因通常是遠端改了本機也有未保存修改的檔案、分支分岔、
或有進行中的 git 操作。把真正的原因告訴使用者,並指出對應的做法:

- 本機未保存的修改如果跟遠端一模一樣(例如兩台都歸檔了同一份交接,換行不同也算),pull 會
  自動改用遠端那份,不會擋下來。
- 真的改得不一樣時,**不要叫使用者先 `/acg save`**:落後遠端時 push 一定被拒絕,那是死路。
  要在資料庫 commit 本機修改後 `git pull --rebase` 解衝突,或放棄本機修改再 pull。
- 分岔就要人工處理。

拉完之後它會顯示狀態。設定與技能還沒套用到各工具家目錄,要套用得另外執行
`acg apply`,那會覆寫本機檔案,所以先問過使用者再做。共用記憶是例外,它拉下來就生效。

引數:`$ARGUMENTS`
