---
name: keepalive
description: 查詢這台機器的用量視窗錨定設定
disable-model-invocation: true
allowed-tools: Bash(acg keepalive status), Bash(ai-config keepalive status)
---

# keepalive:查用量視窗錨定設定

執行 `acg keepalive status`,把輸出整理給使用者。三個工具(claude / codex / agy)各列一行。

用量視窗長五小時,從那個帳號當天第一次呼叫起算,所以第一次呼叫落在哪個鐘點,接下來整天
的邊界就落在哪裡。keepalive 在選定的時間送一句即丟的提示把視窗點著,讓邊界避開工作時段。
每次都用那個工具最便宜的模型、最低的思考強度,因為這通呼叫存在的意義只是「它發生過」。

claude 用程式裡設定的 Haiku;agy 與 codex 從工具自己的清單挑:agy 的額度分三組(Gemini Flash、
Gemini 4、Claude 與 GPT 共用一組),每組各打一次最便宜的(Flash low、Argon low、GPT-OSS),
每組各用一個每月對話;codex 每個帳號從自己的 `models_cache.json` 挑排序最後的那個(快取沒有價格,
這是推測)。日誌會記下挑了哪個。

agy 每次 `-p` 都會存成一個對話,而且沒有刪除指令,所以 keepalive 每台每個月只開一個
對話,之後都接著它,並在 `~/.local/state/acg/keepalive-agy` 執行,不會混進使用者在各專案
`/resume` 看到的清單。使用者說 agy 的對話清單裡有一堆「hi」,那是舊版留下的。

codex 有好幾個帳號時(`~/.codex-work`、`~/.codex-personal` 這類各自有 `auth.json` 的目錄),每個
帳號各自喚醒,status 會各列一行。帳號是自動找的,新增一個 `~/.codex-xxx` 不用改任何設定。

兩個維度都是獨立的,不要建議「統一」:每台機器各自設定,因為各台作息不同;每個工具各自
設定,三個帳號三個視窗,邊界沒有理由對齊。

最近一次是 `exit 1` 的話,後面那行就是工具自己的錯誤訊息(例如額度用完、什麼時候恢復),
照著講給使用者聽。失敗的那一次沒有錨定視窗,邊界會改由下一次真正使用的時間決定;reset
時間偏離排程,通常就是這樣來的。錯誤是「額度用完」的話,下一行會寫 `its window is already
running`:那個帳號的視窗本來就在跑,不算排程失敗,systemd/工作排程器也不會標成失敗。

claude 那行底下的「目前視窗 HH:MM–HH:MM」是 Claude Code 自己回報的實際視窗。如果跳出
「視窗不是從排程的 … 開始」,表示那次 keepalive 雖然成功,卻落在一個更早就被打開的視窗裡:
同一個帳號在別台機器、網頁或手機上先用過了。額度屬於帳號,所以要問的是「那個時間誰用了
這個帳號」,不是 keepalive 壞了。

沒啟用就照實說,啟用方式是 `acg keepalive enable [HH:MM ...] [工具]`,不指定工具就是
claude,不指定時間就用預設的四個。不要替使用者決定時間,那取決於他幾點在電腦前。

視窗屬於帳號不屬於機器,同一個帳號下一台啟用就夠了。使用者問要不要在別台也開時,提醒他
多開只是重複點同一個視窗,不會多拿到額度。

如果輸出提到 claude-scheduler 的排程還在,告訴使用者兩個都開著會在同一時間各點一次火,
要先移除舊的。

引數:`$ARGUMENTS`
