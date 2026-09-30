---
name: acg
description: 用 acg 管理跨機器、跨工具(Claude Code、Codex、Antigravity)的 AI 設定、技能、共用記憶與工作線交接。使用者打 /acg 或 /acg <子指令> 時使用;也在使用者說「交接」「收工」「context 快滿了」「要 clear 了」「接手」「接著做」「上次做到哪」「同步設定」「把設定存起來」「分享技能給 Codex」「傳話給另一個 session」時使用。
argument-hint: '[status|sync|save|share|memory|keepalive|autoupdate|msg|handoff|handoffs|pickup]'
allowed-tools: Read(~/.claude/plugins/cache/acg/**), Bash(acg status:*), Bash(ai-config status:*), Bash(acg pull:*), Bash(ai-config pull:*), Bash(acg push:*), Bash(ai-config push:*), Bash(acg share:*), Bash(ai-config share:*), Bash(acg unshare:*), Bash(ai-config unshare:*), Bash(acg memory status), Bash(ai-config memory status), Bash(acg memory handoff:*), Bash(ai-config memory handoff:*), Bash(acg keepalive status), Bash(ai-config keepalive status), Bash(acg autoupdate status), Bash(ai-config autoupdate status), Bash(acg msg list), Bash(ai-config msg list), Bash(acg skill), Bash(ai-config skill)
---

# acg

acg 是跨機器、跨工具的 AI 設定管理 CLI。私人資料庫是設定的來源;`apply` 把它部署到
各工具的家目錄,`push` 把本機的改動存回去。

使用者的引數:`$ARGUMENTS`

第一個詞是子指令,其餘是它的引數。依子指令讀同一個 plugin 裡對應的做法檔,照著做。
那些檔也是選單上的 `/acg:<子指令>`;讀的時候略過開頭的 frontmatter,檔中的 `$ARGUMENTS`
指的是這裡子指令後面的引數:

| 子指令 | 做法 | 用途 |
| --- | --- | --- |
| `status` | `../status/SKILL.md` | 看這台設定與資料庫差在哪(唯讀) |
| `sync` | `../sync/SKILL.md` | 把資料庫的更新拉下來 |
| `save` | `../save/SKILL.md` | 收集這台的設定,審閱後提交並上傳 |
| `share` | `../share/SKILL.md` | 把 Claude 技能分享給 Codex 與 Antigravity |
| `memory` | `../memory/SKILL.md` | 看共用記憶的狀態 |
| `keepalive` | `../keepalive/SKILL.md` | 查用量視窗錨定設定 |
| `autoupdate` | `../autoupdate/SKILL.md` | 查每天自動更新 acg 與各家 AI CLI 的設定與結果 |
| `msg` | `../msg/SKILL.md` | 傳話給另一個正在跑的 session,或列出誰在線上 |
| `handoff` | `../handoff/SKILL.md` | 把這條工作線交接給下一個 session,或管理 context 提醒 |
| `handoffs` | `../handoffs/SKILL.md` | 列出這個專案還沒被接手的工作線 |
| `pickup` | `../pickup/SKILL.md` | 接手一條交接出來的工作線 |

沒給子指令時,依使用者的話判斷:說「交接」「收工」「要 clear 了」是 `handoff`,
說「接手」「接著做」「上次做到哪」是 `pickup`。判斷不出來就把上表列給使用者選。

問題超出上表時(例如某個指令的旗標、同步規則、在新機器上安裝),執行 `acg skill`
讀完整的使用說明再回答。它隨 CLI 版本更新,比這裡的摘要準。

`apply`、`push`、`reset` 會改寫檔案或對外上傳,做之前先讓使用者看過要變的內容。
