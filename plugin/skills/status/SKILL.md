---
name: status
description: 看這台機器的設定與資料庫差在哪(唯讀)
argument-hint: '[claude|codex|agy|all]'
disable-model-invocation: true
allowed-tools: Bash(acg status:*), Bash(ai-config status:*)
---

# status:看這台與資料庫差在哪(唯讀)

執行 `acg status [claude|codex|agy|all]`,沒指定工具就不帶。

把差異整理給使用者看,分成三類:只在本機的、只在資料庫的、兩邊都有但內容不同的。

另外一類:標著「this acg writes it differently」或「shared hooks; apply updates it」的,
是資料庫沒變、但這一版 acg 寫出來的樣子不同(通常剛更新過 acg)。這類只要 `/acg sync`
重新套用就好,不用推任何東西上去。

標著「old skill copies acg wrote before the folder moved」的,是 acg 以前寫在舊技能目錄
(`~/.codex/skills`、`~/.gemini/antigravity/skills`)的副本,工具會跟新位置的一起載入,
同一個技能就出現兩次。`acg apply codex` / `acg apply agy` 會先備份再移除(apply 會覆寫本機,
先問過使用者);使用者自己裝在那裡的不會動。

這個指令不會改任何東西。若使用者接著想同步,提醒他方向不同:`/acg sync` 是把資料庫
拉下來,`/acg save` 是把本機推上去。不要替他決定方向。

引數:`$ARGUMENTS`
