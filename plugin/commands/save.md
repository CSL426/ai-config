---
description: 把這台的設定收集起來,審閱後提交並上傳
argument-hint: '[claude|codex|agy|all]'
allowed-tools: Bash(acg push:*), Bash(ai-config push:*)
---

使用者要把這台機器的設定保存到資料庫並上傳。

這個動作會 commit 並 push,是對外的。**執行前先讓使用者看過要保存什麼**,不要直接推。

先看差異:

!`acg status $ARGUMENTS`

把變更列給使用者,等他確認之後再執行 `acg push $ARGUMENTS`。

注意事項:

- push 會停在確認提示。若這個 session 沒有終端機,加 `--force` 讓它自動同意,但那表示使用者已經在上一步看過內容了。
- 落後遠端時會被拒絕,要先 `/acg:sync`。
- 若它擋下憑證內容,不要用 `--allow-secrets` 繞過。把它抓到的檔案告訴使用者,讓他自己判斷。
