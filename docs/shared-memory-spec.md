# 全域共用記憶

狀態：Draft。指令已有第一版實作（memory status／enable／disable／path／push），
待使用者在新會話實測後定案。

## 已確認的需求

由同一套 acg 管理 skill 與 memory。兩者放在同一個私人資料根目錄，
使用不同子目錄，納入同一個 Git repository。功能操作、狀態與備份
分開管理；不拆成兩個資料 repo。

設定與 skill 維持拉取與套用分開：使用者可以先更新資料庫、查看
差異，再選擇是否套用、套用到哪些工具，也可以全不套用。

記憶不適用這條分離。設定檔壞掉會弄壞工具，記憶只是純文字筆記，
pull 進來最壞的結果是一則舊筆記。因此記憶不設第二份副本，pull
直接更新使用中的記憶，衝突交給 Git 的三方合併處理。這個決定省掉
init／apply 兩個指令、位元組指紋比對與本機保留路徑防護。

Claude Code、Codex 與 Antigravity CLI（agy）共用同一份記憶。
預設由使用者說「記住」時更新，記錄已確認的結論、日期、適用範圍
與來源。這不等於合併聊天紀錄或各工具的內建自動記憶。

記憶分全域與專案兩層，兩層都由 acg 管理、都進 Git、都跨機器。
全域放使用者偏好與跨專案的事實；專案放某個程式庫自己的決定、
陷阱與進度。各工具內建的自動記憶按本機路徑分檔、不會同步，
不能拿來當專案記憶。

## 前置條件

資料 repo 必須排除在 Git 以外的檔案同步之外。兩台機器之間曾實證
有檔案級同步在搬整個 `~/ai-config`，連未提交修改與 `.git/` 一起搬。
只要那個同步還在，記憶的 Git 合併與 dirty 判斷就沒有意義，兩邊
會互相覆寫。此條件未確認前不啟用記憶功能。

## 儲存與連結

沿用現有私人 data repository 與 skill 目錄，新增同層的 memory。
本次不遷移既有 skill 結構。以下 data-repo 是 acg 設定的資料位置，
與公開工具原始碼 repository 分開。

```text
<data-repo>/                    同一個私人 Git repository
├── .git/
├── claude/
│   ├── skills/                 既有 Claude skills
│   └── shared/                 既有跨工具 skills
├── codex/                      既有 Codex 設定與 skills
├── agy/                        既有 agy 設定與 skills
└── memory/                     三方正在使用的唯一記憶目錄，Git 追蹤
    ├── MEMORY.md               全域索引與全域知識
    ├── topics/                 全域主題文件，按需讀取
    └── projects/
        └── <owner>--<repo>/    一個專案一個目錄
            ├── MEMORY.md       專案索引
            └── topics/         專案主題文件

~/.claude/shared-memory ---------> <data-repo>/memory/
```

只建一個連結。三方的規則入口在實際機器上是同一個檔案（Codex 的
AGENTS.md 與 IDE 的 GEMINI.md 都軟連結到 `~/.claude/CLAUDE.md`），
共用的規則文字只能寫一個路徑；三個連結名字不同反而沒有用處。
Codex 與 agy 透過同一段規則文字讀取這個路徑，不需要各自的連結。

連結只在各機器本地建立，Git 保存普通記憶文件，不保存本機絕對
連結。Linux/macOS 使用目錄軟連結，Windows 使用 Junction。連結
無法建立時明確回報，可讓規則直接指向資料 repo 內的實體目錄，
不用多份可寫副本假裝共用。既有 skill 的 gather／projection 流程
維持原樣。

專案目錄的鍵值取自該專案 `origin` 遠端 URL 正規化後的
`owner/repo`，寫成 `<owner>--<repo>`；同一個程式庫在兩台機器上
路徑不同，但遠端相同，所以鍵值跨機器穩定。沒有遠端的目錄退回
目錄名稱，status 要標明這種鍵值不跨機器。鍵值的推導由 acg 提供
`acg memory path` 印出，規則文字也要寫出推導方式，讓沒有 acg 在
PATH 上的工具也能自己算。

「記住」寫入的是 `<data-repo>/memory/` 的 working tree，對 Git
而言就是一般的未提交修改。Google Drive provider 用 git bundle
傳輸，只帶已提交的歷史，所以未推送的記憶不會經由 Drive 外洩，
也不需要額外排除規則。

## 提議的指令契約

| 指令 | 行為 |
| --- | --- |
| `acg memory status` | 唯讀顯示記憶目錄的 Git 變更、連結與規則入口狀態、各工具入口是否啟用、目前目錄對應的專案鍵值與是否已有專案記憶 |
| `acg memory path` | 印出全域記憶路徑與目前目錄的專案記憶路徑，供規則文字與腳本使用 |
| `acg memory enable` | 預檢、備份、建立缺少的索引與連結、安裝讀寫規則；不提交、不推送 |
| `acg memory disable` | 移除 acg 管理的連結與規則區塊，保留資料庫中的記憶 |
| `acg memory push` | 檢視 memory/ 的變更，確認後提交；推送前確認完整待推送範圍 |
| `acg pull` | 更新整個共用資料 repository；設定與 skill 顯示差異不套用，記憶直接生效 |

memory 是獨立命令群組，不是第四個 AI 工具。skill 的分享、部署與
清理不連帶改寫 memory，memory 的啟用／停用不操作 skill。
現有 skill 指令保留，不因本提案重新命名。GUI 分開呈現兩種功能。

設定與 skill 的套用流程提供類別選擇，再選 Claude、Codex、agy 的
目標。取消或未選的類別維持本機內容；不得因為 pull 成功就自動
套用全部。CLI 需提供等價的明確範圍選項；既有 apply 目前按工具
選擇，類別篩選是本提案需補上的功能，不能宣稱已存在。記憶不進入
套用流程。

memory 內容是三方共用的，pull 進來的內容會影響所有已啟用的記憶
入口；工具選擇只決定哪些入口啟用，不能承諾同一份記憶只更新其中
一方。

首次啟用遇到同名普通目錄、外部連結或不同內容時，列出衝突並保留
原始資料。enable 不自動匯入各工具內建自動記憶。

## 共用 Git 的行為

功能分開管理不代表 Git branch 分開。pull 更新整個 branch，因此
可能同時更新設定、skill 的同步版本與記憶；前兩者不改本機使用
中的版本，記憶直接生效。Git 拉取是 repository 層級，設定與 skill
的選擇性生效由套用範圍控制。

pull 前檢查未提交修改與進行中的 Git 操作，先讓使用者處理變更，
不自動 stash、丟棄或覆蓋使用者修改。現有守衛已經只看已追蹤檔案
的修改；memory/ 是已追蹤目錄，有未提交的「記住」時 pull 會被擋，
提示應明確指向 `acg memory push`。合併衝突由 Git 標記，acg 列出
衝突檔案並要求使用者處理，不自動採用任一版本。檢查到寫入之間
仍需驗證內容變化，不能承諾 acg 的鎖能阻止外部工具修改文件。

提交可按 skill／設定與 memory 分開，但 Git push 推送完整 commit
歷史。若 branch 已有其他類別的未推送提交，必須列出完整範圍並
取得確認，或要求先處理；不能宣稱 memory push 只上傳 memory。
預先暫存的其他變更維持現有拒絕規則，不得混入記憶提交。

「記住」會讓資料 repo 變髒。現有 push 預檢在「選定工具以外有未
提交路徑」時直接取消，所以 memory 必須成為 push 的一個可選範圍：
`acg push memory` 只提交記憶；`acg push claude` 遇到 memory/ 的
修改時列出並要求先處理或一起推；`acg push all` 涵蓋記憶。記憶
在原地編輯，沒有 gather 步驟，既有工具的 gather 流程不變。

## 載入與更新

規則入口只有一段固定文字。有了 `~/.claude/shared-memory` 這個
機器無關的路徑，管理區塊就是常數字串，直接放進已受 acg 同步的
`~/.claude/CLAUDE.md` 即可；不需要按本機位置生成，gather 時也不
需要過濾。Codex 在沒有專用 AGENTS.md 時本來就投影 CLAUDE.md，
Codex 的 AGENTS.override.md 若遮蔽入口，status 必須指出。

agy CLI 沒有 GEMINI.md 形式的全域規則檔。它內建的說明列出三個
探索位置：專案內的 `.agents/`、往上走到 repo 根目錄的 GEMINI.md／
AGENTS.md，以及對所有專案生效的全域根目錄 `~/.gemini/config/`。
IDE 文件列出的 `~/.gemini/GEMINI.md` 從 repo 內啟動的 agy CLI 走不
到。因此 agy 的入口是 `~/.gemini/config/rules/acg-memory.md`，內容
就是同一段規則文字，由 enable 寫入、disable 移除；acg 已經用
`~/.gemini/config/skills` 當 agy 的技能位置，這是同一個根目錄。
是否真的被新會話載入仍要以實測為準。

管理區塊要求先讀取共用目錄的 MEMORY.md，再算出目前專案的鍵值
並讀取 `projects/<鍵值>/MEMORY.md`（不存在就略過），然後按兩份
索引讀取相關主題。只有使用者要求記住時更新，記錄結論、日期、
來源與適用範圍，避免重複與未證實猜測。不保存憑證、完整聊天紀錄
或暫時狀態。

「記住」的分層由內容決定：講的是這個程式庫的決定、陷阱或進度就
寫專案層，講的是使用者偏好或跨專案的事實就寫全域層；分不清時問
使用者，不自行猜測。專案層目錄不存在時由模型建立索引檔，acg 不
需要事先替每個專案開目錄。三方使用明確讀取指示，不
假設都支援 Claude 的 @import 語法。

Claude Code 內建的自動記憶放在 `~/.claude/projects/<專案路徑>/memory/`，
按本機路徑分檔、不跨機器，只與本提案的專案層重疊。管理區塊必須
寫清楚歸屬，否則模型會兩邊都寫或寫錯邊：使用者說「記住」時唯一
目的地是共用記憶的全域層或專案層；自動記憶只留給工具在未被要求
時的觀察，不再視為專案記憶。enable 不搬移自動記憶的內容，匯入是
另一個提案的範圍。remember plugin 是專案工作日誌，不在本提案範圍。

規則是模型指示，無法保證載入或防止多個 AI 同時改寫。寫入前檢查
讀取後的內容變化，使用原子寫入；驗收需有新會話的實際讀寫證據。

## 安全與備份

記憶使用獨立資源處理器，不塞入 CLAUDE_MANAGED_DIRS 或 shared skill
清理流程。沿用憑證排除、內容掃描、檢視後確認與範圍驗證機制。

預檢驗證父路徑、資料庫邊界、目錄類型、symlink／Junction 的直接
目標，以及內容中的 reparse point、斷鏈與越界。未知同名內容不能
自動接管。記憶目錄暫時不存在不可被當成刪除全部記憶的指令。

備份保存實際內容與連結資訊，按功能區分復原範圍；skill 的備份
清理不刪除 memory 快照。所有目標先預檢，再執行寫入。Git 歷史不
代替未提交內容的備份，enable／disable 不自動提交。

## 程式整合位置

- paths.py：沿用 SCRIPT_DIR 作為共用資料根目錄，新增 memory/ 子路徑
  與 `~/.claude/shared-memory` 連結位置。
- 新增 memory.py 與 commands/memory.py：記憶狀態、連結與啟用生命週期，
  以及專案鍵值推導與 `memory path`。
- safety.py、links.py、backup.py：邊界驗證、Junction、記憶內容備份。
- tools/claude.py：CLAUDE.md 管理區塊的安裝、辨識與移除。
- tools/codex.py：AGENTS.override.md 遮蔽偵測。
- memory.py：agy 的 `~/.gemini/config/rules/acg-memory.md` 寫入與移除。
- commands/push.py：memory 範圍、跨類別待推送提交的完整檢查。
- commands/sync.py：memory/ dirty 時的提示與合併衝突列表。
- commands/apply.py 與 projection／備份：設定與 skill 的類別選擇；
  記憶不進入寫入或清理流程。
- __main__.py、completion.py、guide.py：命令、補全與內建說明。
- GUI 與同步 provider：共用資料連線，分開顯示功能狀態與操作。

## 驗收

隔離 HOME 與私人資料 repo，測試首次／重複啟用、停用保留內容、
三方任一方寫入後另外兩方可見、「記住」後 Git 可看到修改、原子
替換後連結有效、資料位置變更、缺失目錄、備份復原、惡意連結與
憑證排除。status 不得寫入。

驗證 skill 的分享／部署／清理／復原不改 memory，反向亦同；
memory dirty 不使 skill gather 被意外略過。驗證單次 pull 更新
設定與 skill 的同步版本但不改 live 版本，同時記憶直接更新；只
套用設定、只套用 skill、全部取消、未選工具不改動。測試 memory/
有未提交修改時 pull 被擋且提示正確、合併衝突被列出、跨類別
pending commits 的完整推送預覽，以及記憶提交不混入其他 staged
變更。

Windows Python 3.11/3.12 使用原生 CI 驗證 Junction 與路徑 identity。
另在新的 Claude/Codex/agy 會話測試讀取兩層索引與保存測試知識，
確認「記住」寫入共用記憶而非自動記憶，專案內容進專案層、偏好進
全域層，且同一程式庫在第二台機器算出相同鍵值；只裝部分工具時，正確回報未
啟用入口。agy 的驗收需以 plugin 規則在新會話實際載入為準。

## 入口依據

- [OpenAI：全域 AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Claude Code：記憶](https://code.claude.com/docs/en/memory)
- agy CLI 內建說明：`~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/rules.md`
  與同目錄的 plugins.md，只列目錄規則與 plugin 規則兩種來源
- [Antigravity IDE：全域 Rules](https://www.antigravity.google/docs/rules-workflows/)，
  適用於 IDE，不適用於 agy CLI

若需求包含各工具內建自動記憶互通，另需定義來源格式、匯入範圍、
來源追蹤及衝突規則；目錄互連不足以達成此需求。

## 與前一版的差異

- 移除 `.local/memory/` 雙副本、`memory init`、`memory apply`、
  指紋比對與本機保留路徑防護；記憶改為直接追蹤，pull 直接生效。
- 三個連結縮為一個 `~/.claude/shared-memory`，規則區塊改為常數，
  不再由本機位置生成。
- agy 入口從全域 GEMINI.md 改為 `~/.gemini/config/rules/` 下的規則檔。
- 新增前置條件：資料 repo 排除在檔案同步之外。
- 新增「記住」在三套記憶之間的歸屬規則。
- 新增專案層記憶：以遠端 URL 推導跨機器穩定的鍵值，兩層都由
  acg 管理並同步。

## 審查修正與驗證邊界

- 啟用／停用先預檢路徑與規則，再取得操作鎖及備份。未知 reparse
  目標或同名 agy 規則會拒絕，不直接接管。
- Codex 獨立 AGENTS.md 與既有 Claude 共用連結皆支援；status 明確
  顯示 Codex 入口是否有管理區塊，override 遮蔽仍另行警示。
- 備份使用唯一快照名稱與來源映射，區分同名的本機／資料庫規則，
  包含 agy 與 remember 設定。失敗時還原已修改入口並保留備份位置。
- 日誌搬移保留所有同名版本，adopt／release 失敗時回復已移動內容
  與連結。adopt 可建立缺少的共用目錄連結；不必先 enable。
- 未啟用 memory 的舊資料庫仍可 push all；整個 memory 根目錄缺失
  會拒絕推送，目錄內單一筆記的有意刪除仍可檢視後提交。

以上是 CLI 修正。GUI 的 memory 操作與設定／skill 類別篩選仍屬待
實作項目。新會話載入與原生 Windows 行為需各自驗證，檔案與隔離
CLI 測試通過不代表已完成三個 AI runtime 的端到端驗收。
