---
name: skills
description: 同步專案自己的技能:只寫在 .agents/skills,複製給 Claude,一份給三個工具用
argument-hint: '[專案目錄] [--check]'
disable-model-invocation: true
allowed-tools: Bash(acg skill sync:*), Bash(ai-config skill sync:*)
---

# skills:專案技能只寫一份

Codex 與 Antigravity 讀專案的 `.agents/skills/`,Claude 只讀 `.claude/skills/`。
技能只寫在 `.agents/skills/<名稱>/`,再執行:

```
acg skill sync [專案目錄]
```

要改技能就改 `.agents/skills/` 那份,不要改 `.claude/skills/` 的副本,然後再同步一次。

- `--check` 不寫入,有任何一項不一致就回傳非 0,適合放進 CI。
- `.claude/skills/` 的副本和專案根目錄的 `.acg-skill-sync.json` 都要跟著專案一起提交。
- 不能在家目錄或資料庫裡跑。`.agents/skills` 整個不見、但紀錄還列著技能時會拒絕;要移除
  最後一個技能,就連副本和紀錄檔一起刪。
- 同名但不是 acg 放的技能,或同步後被改過的副本,都不會被覆蓋,結束碼是 1。把輸出裡的
  衝突告訴使用者,由使用者決定留哪一份:要用來源那份,就先刪掉 `.claude/skills/` 裡的副本再同步。
- 來源刪掉後,沒被改過的副本會一起移除。

這個指令只動專案目錄,不讀資料庫,也不碰家目錄。要把資料庫的技能放進專案是 `acg deploy`,
不是這個。

引數:`$ARGUMENTS`
