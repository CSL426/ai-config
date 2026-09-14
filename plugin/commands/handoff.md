---
description: 把這條工作線的進度交接給下一個 session
argument-hint: '[工作線名稱] [進度與下一步]'
allowed-tools: Bash(acg memory handoff:*), Bash(ai-config memory handoff:*)
---

使用者要把目前這條工作線交接出去,給接手的下一個 session 讀。

引數:`$ARGUMENTS`

第一個詞是工作線名稱,其餘是內容。若使用者沒給,或給得不完整,你要自己補:

- **名稱**:用這個 session 實際在做的事命名,簡短好認,例如「記憶改善」「GUI 改版」。不要用 session id。
- **內容**:寫給不知道前因後果的人看。必須包含做完了什麼、現在卡在哪、下一步該做什麼。不要只寫「繼續」這種沒有資訊的句子。

寫入指令(名稱含空格要用引號):

!`acg memory handoff write "<名稱>" "<內容>"`

寫完把結果回報給使用者,並提醒下個 session 可以用 `/acg:pickup` 接手。
