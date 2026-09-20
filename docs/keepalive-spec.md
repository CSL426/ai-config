# 用量視窗錨定(keepalive)規格

狀態：**Draft**。使用者確認前不改為 Done。

## 目的與範圍

Claude 的用量視窗長五小時，從那個帳號當天第一次呼叫起算。第一次呼叫落在哪個
鐘點，接下來整天的視窗邊界就落在哪裡。想讓視窗邊界避開工作時段，就得在選定的
時間主動發一次最小的呼叫把視窗點著。

`keepalive` 做的就是這件事：在每台機器各自選定的幾個時間，用最便宜的模型送出
一句即丟的提示，除了起算視窗之外不產生任何東西。

**不在範圍內**：這不是工作排程器，不執行使用者的指令，不讀寫資料庫，不同步
任何設定。它唯一的產出是一行日誌。

既有工具 `ccs`(`~/claude-scheduler`)已經在做同一件事，本規格取代它。遷移見
最後一節。

## 為什麼要搬進 acg

`ccs` 本身沒有壞，四個平台後端、測試與 CI 都齊。搬過來的理由只有一個：
**它用 cron，而 cron 不補跑。**

| | `ccs`(cron) | acg 排程(systemd timer) |
| --- | --- | --- |
| 機器在該時段休眠 | 那次永久漏掉 | `Persistent=true` 開機後補跑 |
| 同時段尖峰 | 無 | `RandomizedDelaySec=600` |
| 卡住的執行 | 無 | `RuntimeMaxSec=900` |

對一個「錨定視窗」的功能來說，漏跑等於整天的視窗都錯位，而且**沒有任何徵兆**
——日誌不會有那一筆，沒人會去數。acg 的排程層已經解決了這件事，重複一套只是
多一份會漏的。

搬過來後 `ccs` 約 1,200 行的平台後端、安裝、更新、補全全部不需要，真正要移植的
是「在指定時間送一句提示」這個意圖。

## 管理入口

| 指令 | 行為 |
| --- | --- |
| `acg keepalive` / `keepalive status` | 查詢本機設定、下次觸發時間、最後一次結果 |
| `acg keepalive enable` | 啟用，使用預設時間 |
| `acg keepalive enable 07:00 12:05 17:10 22:15` | 啟用並指定時間，接受 1 到 8 個 `HH:MM` |
| `acg keepalive disable` | 停用並移除 acg 安裝的排程 |
| `acg keepalive run` | 立即送一次，供驗證用 |

預設停用，每台機器獨立選擇啟用。CLI、`--help`、agent guide、Desktop 與
`/acg:keepalive` 提供相同語意，CLI 與 plugin 版本保持一致。

## 設定與狀態

沿用既有的每機一檔：`~/.claude/shared-memory/autopush-schedule/<hostname>.toml`
增加一個獨立區段，不動現有欄位。

```toml
host = "gpu-a4000"
slot = "04:10"          # autopush 的，不受影響

[keepalive]
times = ["07:00", "12:05", "17:10", "22:15"]
model = "claude-haiku-4-5-20251001"
prompt = "reply with only the word: hi"
```

- **每台機器的時間各自獨立**，因為作息不同。沿用每機一檔正是為了這個。
- `model` 可設定。硬編碼的模型遲早退役，預設值單獨放一行方便改。
- 日誌寫本機 `~/.local/state/acg/keepalive.log`，**不進共用資料庫**——那是
  每台各自的執行紀錄，同步過去只會互相覆蓋。

## 排程機制

重用 `ai_config/autopush.py` 既有的三平台安裝層(systemd / launchd / schtasks)。

目前那一層把單位名稱寫死在模組層級：

```python
_LABEL = "com.csl426.acg.autopush"
_UNIT = "acg-autopush"
_TASK = "acg memory autopush"
```

**這是唯一需要動既有程式的地方**：把單位名稱與要執行的指令改成參數，讓
autopush 與 keepalive 各自傳入自己的名字。autopush 的行為必須完全不變，
單位名稱也必須維持 `acg-autopush`，否則既有三台機器的排程會變成孤兒。

keepalive 一天有多個時間點，autopush 只有一個。systemd 一個 timer 可以有多個
`OnCalendar=`，launchd 的 `StartCalendarInterval` 接受陣列，Windows 則是每個
時間一個 task(`acg keepalive HHMM`)。

## 執行

```
claude --model <model> -p "<prompt>"
```

- 找 `claude` 執行檔的方式沿用 `ccs` 已經處理好的邏輯(`runner.py`)：Claude Code
  會裝成版本目錄加一個穩定啟動器，直接記住某個版本路徑會在下次更新後失效。
  npm shim 要用 node 執行 `cli.js`。這段是真的踩過坑才寫出來的，值得照搬。
- **不儲存任何憑證**，依賴機器上既有的登入。
- 失敗只寫日誌，不重試、不通知。這個功能漏一次的代價是視窗晚點起算，不值得
  為它叫醒任何人。

## 測試

- 時間字串的解析與驗證(含超出範圍、重複、超過 8 個)
- 三平台各自產生的排程內容(比照 `systemd_units` / `launchd_plist` /
  `schtasks_argv` 既有測試)
- **autopush 的單位名稱在重構後沒有改變** ——這條是防止重構把既有排程弄成孤兒
- 設定檔加入 `[keepalive]` 之後，既有 autopush 欄位仍讀得出來

## 遷移

`ccs` 與 keepalive 若同時啟用，會在同一時間各點一次火，一天燒兩倍。

啟用 keepalive 時必須偵測既有的 `ccs` 排程並處理：

1. 偵測 crontab 裡 `# BEGIN claude-scheduler` 標記區塊(Windows 與 macOS 各自
   對應的 task / launchd label)
2. **偵測到就停下來說明，不自動移除**——那是另一個工具安裝的東西，acg 不該
   替它做決定。提示使用者跑 `ccs remove` 或手動清掉，再重新啟用
3. 可用 `acg keepalive enable --replace-ccs` 明確授權自動移除

已有的 `~/.config/claude-scheduler/config.json` 可以讀來當預設值，省去重打四個
時間。讀取失敗就用內建預設，不要因此讓啟用失敗。

## 影響的介面

CLI、`--help`(`usage()`)、agent guide、Desktop(`gui/` 與 `gui_management.py`、
`commands/gui.py`)、plugin(`plugin/commands/` 與 `plugin.json` 版號)。

Codex 與 Antigravity 不安裝此功能——它錨定的是 Claude 的用量視窗。
