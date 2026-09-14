---
description: 接手一條交接出來的工作線,並讀取它的進度
argument-hint: '[工作線名稱]'
allowed-tools: Bash(acg memory handoff:*), Bash(ai-config memory handoff:*)
---

使用者要接手一條先前交接出來的工作線。

引數:`$ARGUMENTS`

若使用者沒指定名稱,先列出有哪些可接:

!`acg memory handoff list`

然後問他要接哪一條,不要自己挑。

指定了名稱就認領它(名稱含空格要用引號):

!`acg memory handoff claim "<名稱>"`

認領會印出那條線的進度。讀完之後,用你自己的話跟使用者確認你理解的接手點,再開始工作。

如果認領被拒絕,表示已經有別的 session 持有它。把持有者告訴使用者,讓他決定要不要強制接手,不要自己重試。
