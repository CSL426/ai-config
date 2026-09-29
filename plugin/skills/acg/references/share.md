# share:把 Claude 技能分享給 Codex 與 Antigravity

執行 `acg share <技能名稱> [--to both|codex|agy]`。

來源可以是 `~/.claude/skills/` 底下的技能,或已安裝外掛帶的技能。找不到會直接報錯,
不要自己猜名稱。

`~/.claude/skills/` 底下、由資料庫管理的技能,apply 時本來就會投影給 Codex 與
Antigravity,不需要分享。分享是給「只想給 Codex 或 Antigravity」或「外掛帶的技能」用的。

預設分享給兩邊,用 `--to codex` 或 `--to agy` 可以只給一邊。分享只是把來源複製進資料庫的
共用區;要讓其他工具真的讀到,還要執行 `acg apply --category skills`,那會寫入其他工具的
家目錄,先問過使用者。

取消分享用 `acg unshare <名稱>`,它只移除共用副本,`~/.claude/skills/` 底下的原檔會留著。
