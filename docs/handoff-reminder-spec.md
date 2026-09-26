# Context 交接提醒規格

狀態：**Draft**。使用者確認前不改為 Done。

## 目的與範圍

Claude Code context 接近滿載時，提醒目前 session 整理進度與下一步，方便後續接手。
提醒只注入提示；實際儲存仍使用既有 `memory handoff write` 或 `/acg:handoff`。
不要求事先認領工作線，不推測目前應寫入哪一條工作線。

`acg memory enable` 會一併以 70% 開啟提醒(已設定的門檻保留),`acg memory disable`
會一併移除;`remind disable` 可以只關提醒、保留共用記憶。每台機器各自設定,
設定、session 讀值及提醒狀態留在本機，不進入共用資料庫。Codex、Antigravity 不安裝此提醒。
提醒設定壞掉時只警告，不阻擋共用記憶啟用。

## 管理入口

| 指令 | 行為 |
| --- | --- |
| `acg memory handoff remind` / `remind status` | 查詢本機設定 |
| `acg memory handoff remind enable` | 啟用，門檻 70% |
| `acg memory handoff remind enable 80` | 啟用並設定門檻，接受 1 到 99 的整數 |
| `acg memory handoff remind disable` | 停用並移除 acg 管理的提醒設定 |

CLI、`--help`、agent guide、Desktop 記憶頁及 `/acg:handoff remind` 都提供相同語意。
CLI 與 plugin 版本保持一致。停用時保留使用者自訂 hooks 與 statusLine。

## 資料與觸發流程

1. statusLine wrapper 讀取 Claude Code 官方輸入的 session ID 及
   `context_window.used_percentage`，依 session 儲存百分比與時間。
   呼叫原 statusLine command，保留原輸出；不解析畫面文字或以 transcript 大小估算。
2. `UserPromptSubmit` 與 `PostToolUse` 讀取目前 session 的有效讀值。
   缺漏、`null`、無效或過期讀值直接略過，不能拿別的 session 的讀值代替。
   讀值超過五分鐘即視為過期。帶有 `agent_id` 的子代理事件略過，
   避免子代理消耗主會話的提醒。
3. 百分比達到門檻時注入一次提醒，提示整理已完成事項、阻礙與下一步。
   同一 session 在同一輪壓縮週期內不重複提醒。
4. `PreCompact` 只重設該 session 的讀值及已提醒狀態，防止舊的高百分比再次觸發。
   不要求模型執行交接、不等待模型、不取消或延後壓縮。
5. 壓縮後收到新的有效讀值，才能在下一輪達到門檻時再次提醒。
   `SessionEnd` 清除該 session 的本機快取。

介面依據：[statusLine 官方欄位](https://code.claude.com/docs/en/statusline#context-window-fields)、
[hook 注入方式](https://code.claude.com/docs/en/hooks#add-context-for-claude)。
statusLine 讀值反映最近一次 API 回應；提醒不保證在自動壓縮前送達。

## 驗收

- 未啟用不安裝提醒；重複啟用不疊加 hooks，停用恢復原 statusLine。
- 官方百分比低於門檻不提醒，到達門檻才提醒，重複事件不重複提示。
- 各 session 獨立；沒有 claimed 工作線也能收到提醒。
- `null`、無效、過期讀值不觸發；PreCompact 清掉舊狀態且不輸出寫入指令。
- 有既有 statusLine 時，wrapper 保留其 stdout；既有使用者 hooks 保留。
- gather/apply 不把本機 wrapper、hooks、設定或讀值帶到其他機器。
- 以實際安裝的 CLI／plugin 驗證管理入口；Windows 行為由原生 Windows 驗證。
