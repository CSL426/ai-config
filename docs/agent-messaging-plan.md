# Agent 之間的傳話

狀態:**Draft**。使用者確認前不改為 Done。

## 目標

讓 Claude Code、Codex、Antigravity 正在跑的 session 能像 Claude 的
`ListAgents` / `SendMessage` 一樣互相傳話:列出誰在線上、用名字指定對象、
對方用同一個方式回覆。使用者照常打 `claude`、`codex`,不必記額外參數。

## 已驗證(2026-09-24,A4000)

- Codex 0.156.1:`codex app-server daemon start` 起的 daemon 在
  `$CODEX_HOME/app-server-control/app-server-control.sock` 上跑
  **WebSocket over unix socket**(HTTP Upgrade 回 101),JSON-RPC。
  `initialize` → `initialized` → `thread/loaded/list` 列出載入中的對話;
  `thread/read` 給名稱、狀態、cwd、`source`(子 agent 的 `source` 是物件,
  要濾掉)。Python 標準函式庫就能連。
- `codex queue --remote unix:// --thread <UUID 或完整名稱> --message` 把訊息
  送進掛在 daemon 上的 TUI(另一個 session 實測,TUI 即時收到並回覆)。
  TUI 必須以 `--remote unix://` 啟動才會掛上 daemon。
- Antigravity 1.2.9:正開著的對話會握住
  `~/.gemini/antigravity-cli/presence/<id>.lock` 的 flock;89 個 lock 檔中
  只有 2 個被握住,各對應一個 `agy` 行程。送訊息進正開著的 TUI 會讓對話
  分岔(另一個 session 實測),所以只能拒送。
- Claude Code:外部程式推訊息進 session 的正式方式是 **channels**
  (MCP server 宣告 `claude/channel`,送 `notifications/claude/channel`),
  研究預覽中;自訂 channel 要 `--dangerously-load-development-channels`,
  只支援 claude.ai 登入。`SendMessage` 的 socket 協定是內部的,不能用。

## 設計

acg 當郵局:

- `acg msg list`:列出三種工具在線上的 session(名稱、工具、帳號、能否收)。
- `acg msg send <名稱> "<訊息>"`:依對象選送法,訊息附上寄件人。
  - Codex → `codex queue`(依帳號設 `CODEX_HOME`)。
  - Claude → 推進該 session 的 acg channel。
  - Antigravity → 對話正開著就拒絕,說明原因。
- 回覆:收件人用 `acg msg send <寄件人>` 回。Codex、Antigravity 都能跑 shell。
- `--wait`:送給 Codex 時可等對方這一輪結束,把回覆印出來(讀
  `thread/turns/list`),給還沒有 channel 的第一步用。

## 啟動方式(不另外帶參數)

- 這台的 `.bashrc` 有使用者自己的 `codex()`(帳號切換),acg 不覆蓋它,
  只在開 TUI 的情況加 `--remote unix://`,daemon 沒開先啟動;`exec`、
  `queue` 等子指令不加。
- acg 管理一個有標記的 shell 區塊:機器上沒有 `codex()` 時由它提供;有的
  話只提示如何併入。`claude()` 同理,加上 channel 參數(第二步)。

## 分步

1. `acg msg list` / `send`,Codex 為收件人,含 `--wait`;這台的 `codex()`
   掛上 daemon。
2. acg 的 Claude channel(`acg __channel`,Python stdio MCP),打通
   Codex/Antigravity → Claude;`claude()` 包裝。
3. Antigravity 列入 `list`,送訊息時以 presence 鎖拒送。

每一步都要走完五個出口:CLI、`--help`、guide、GUI(不需要,這不是 GUI 的
用途——明說)、plugin。

## 限制與未知

- Codex 的 app-server、`queue`、rollout 格式是內部介面,升版可能變。
- daemon 是 pid backend,重開機就沒了;`bootstrap` 未測。
- Windows 上 Codex 是否用 unix socket 未確認;先做 Linux / macOS。
- 跨機器傳話不在範圍內。
