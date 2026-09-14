---
description: 把一個 Claude 技能分享給 Codex 與 Antigravity
argument-hint: '<技能名稱> [--to both|codex|agy]'
allowed-tools: Bash(acg share:*), Bash(ai-config share:*), Bash(acg unshare:*), Bash(ai-config unshare:*)
---

使用者要把一個技能變成跨工具共用的。

引數:`$ARGUMENTS`

來源可以是 `~/.claude/skills/` 底下的技能,或已安裝外掛帶的技能。找不到會直接報錯,不要自己猜名稱。

!`acg share $ARGUMENTS`

預設分享給 Codex 和 Antigravity 兩邊,用 `--to codex` 或 `--to agy` 可以只給一邊。

分享只是把來源複製進資料庫的共用區。要讓其他工具真的讀到,還要執行 `acg apply --category skills`。那會寫入其他工具的家目錄,先問過使用者。

要取消分享用 `acg unshare <名稱>`,它只移除共用副本,`~/.claude/skills/` 底下的原檔會留著。
