---
name: handoffs
description: 列出這個專案還沒被接手的工作線
disable-model-invocation: true
allowed-tools: Bash(acg memory handoff list), Bash(ai-config memory handoff list)
---

# handoffs:列出還沒被接手的工作線

執行 `acg memory handoff list`,把輸出整理給使用者看。`○` 表示還沒人接;被認領的線會
直接結案,不會出現在清單上。名稱後面的天數是這條線開了多久,放越久越值得問使用者要不要接。

一個月沒更新的線(不論是否結束)會在列表跑的時候自動搬進 `handoff/archive/`,紀錄留著,
只是不再擋在工作目錄裡。

標著 `⚠ 可能已過期` 的線超過一天沒人動過,它寫的進度可能已經被別處的工作蓋過去了。
接手前要先讀一遍內容、對照現況,不要照著它的待辦直接做。

清單是空的就直接說這個專案沒有待接手的工作線,不要多加臆測。

引數:`$ARGUMENTS`
