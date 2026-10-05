---
name: save
description: 把這台的設定收集起來,審閱後提交並上傳
argument-hint: '[claude|codex|agy|all]'
disable-model-invocation: true
allowed-tools: Bash(acg status:*), Bash(ai-config status:*), Bash(acg push:*), Bash(ai-config push:*)
---

# save:收集這台的設定,審閱後提交並上傳

這個動作會 commit 並 push,是對外的。先讓使用者看過要保存什麼,不要直接推。

1. 執行 `acg status [工具]`,把變更列給使用者。
2. 他確認之後,執行 `acg push [工具]`。

- push 會停在確認提示。若這個 session 沒有終端機,加 `--force` 讓它自動同意;
  那表示使用者已經在上一步看過內容了。
- 落後遠端時會被拒絕,要先 `/acg sync`。
- 若它擋下憑證內容,不要用 `--allow-secrets` 繞過。把它抓到的檔案告訴使用者,讓他
  自己判斷。
- 若它說「這台上次 apply 之後,資料庫又收到其他機器的設定更新」,表示這台的設定
  比資料庫舊,push 會把別台的更新蓋回去。`--force` 跳不過這一步。把列出的更新告訴
  使用者,建議先 `acg apply <工具>` 再 push;要不要覆蓋由他在終端機自己回答。

引數:`$ARGUMENTS`
