---
name: repos
description: 查這台每晚同步哪些 git repo(例如知識庫)與上次結果,或加入、移除、立即同步
disable-model-invocation: true
allowed-tools: Bash(acg repos list), Bash(ai-config repos list), Bash(acg repos sync:*), Bash(ai-config repos sync:*)
---

# repos:每晚同步的 git repo

先執行 `acg repos list`,告訴使用者這台登記了哪些 repo、每晚排程有沒有開、每個 repo 上次
同步的結果(✓ 或 ✗ 與原因)。

使用者要加入或移除時,用 `acg repos add <路徑>`、`acg repos remove <路徑>`。路徑要是使用者
說的那個,不要自己挑;清單每台各自一份,不會同步到別台,所以別台要同步得在別台另外加。
加入第一個 repo 會順便打開每晚排程的這一半(跟記憶上傳、自動更新共用同一個時段),移除最後
一個會關掉它。acg 自己的資料庫與共用記憶會被拒絕,它們走 `acg push` 的審查流程。

每晚的做法:把未提交的變更(遵守 `.gitignore`)以 repo 自己的 git 身分提交成
`chore: acg repos sync (<主機>)`,抓遠端後 rebase 上去,再 push。不會 force push,也不會
自己解衝突。以下情況那個 repo 會原封不動地停下來,其他 repo 照常:

- rebase 衝突:已經 `rebase --abort` 還原,本機變更在那個 commit 裡沒有遺失。請使用者自己
  `git pull --rebase` 解衝突後 push。
- 要提交的檔案有一行像憑證:這次什麼都沒提交。先自己讀那幾個檔找出觸發的那行;確定不是
  金鑰就請使用者自己 commit,是金鑰就移走或加進 `.gitignore`。不要替使用者提交。
- 裡面有一個沒追蹤的 git clone(會變成壞掉的子模組):請使用者加進 `.gitignore` 或搬出去。
- 正在 rebase/merge/cherry-pick、有沒解完的衝突(`stash pop` 衝突也算)、有 `index.lock`、
  detached HEAD 或沒有追蹤遠端、push 目的地跟追蹤的遠端不同(fork 流程)或分支名不同、push 失敗。

使用者自己 commit 但還沒 push 的內容,同步時會一起推上去(不另外掃憑證);登記 repo
就等於同意這個分支會被推。

停下來的 repo 會在新 session 開頭提醒,直到 `acg repos sync <路徑>` 成功。使用者處理完
要你重跑時才跑 `acg repos sync <路徑>`;不帶路徑會同步整份清單。

引數:`$ARGUMENTS`
