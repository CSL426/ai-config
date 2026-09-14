---
description: 看這台機器的設定與資料庫差在哪(唯讀)
argument-hint: '[claude|codex|agy|all]'
disable-model-invocation: true
allowed-tools: Bash(acg status:*), Bash(ai-config status:*)
---

!`acg status $ARGUMENTS`

把差異整理給使用者看,分成三類:只在本機的、只在資料庫的、兩邊都有但內容不同的。

這個指令是唯讀的,不會改任何東西。若使用者接著想同步,提醒他方向不同:`/acg:sync` 是把資料庫拉下來,`/acg:save` 是把本機推上去。不要自己替他決定方向。
