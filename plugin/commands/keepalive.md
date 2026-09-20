---
description: 查詢這台機器的用量視窗錨定設定
disable-model-invocation: true
allowed-tools: Bash(acg keepalive status), Bash(ai-config keepalive status)
---

!`acg keepalive status`

把輸出整理給使用者看。

Claude 的用量視窗長五小時,從那個帳號當天第一次呼叫起算,所以第一次呼叫落在
哪個鐘點,接下來整天的邊界就落在哪裡。keepalive 在選定的時間送一句即丟的提示
把視窗點著,讓邊界避開工作時段。

這是**每台機器各自**的設定——各台作息不同,時間本來就該不一樣。所以看到別台
的時間跟這台不同,那是正常的,不要建議「統一」。

沒啟用就照實說,並告訴他啟用方式是 `acg keepalive enable [HH:MM ...]`,不指定
時間就用預設的四個。**不要自己幫他決定時間**,那取決於他幾點在電腦前。

如果輸出提到 claude-scheduler 的排程還在,告訴使用者兩個都開著會在同一時間
各點一次火,要先移除舊的。
