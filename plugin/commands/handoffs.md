---
description: 列出這個專案還沒被接手的工作線
disable-model-invocation: true
allowed-tools: Bash(acg memory handoff list), Bash(ai-config memory handoff list)
---

!`acg memory handoff list`

把輸出整理給使用者看。狀態符號的意思:`○` 還沒人接、`◐` 已被某個 session 認領。
已結束的線不會出現在這份清單裡。名稱後面的天數是這條線開了多久,放越久越
值得問使用者要不要接。

結束超過一個月的線會在列表跑的時候自動搬進 `handoff/archive/`,紀錄留著,
只是不再擋在工作目錄裡。

標著 `⚠ 可能已過期` 的線超過一天沒人動過。它裡面寫的進度可能已經被別處的
工作蓋過去了 —— 接手前要先讀一遍內容、對照現況,不要照著它的待辦直接做。

如果清單是空的,直接說這個專案沒有待接手的工作線,不要多加臆測。
