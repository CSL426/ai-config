# GUI 記憶管理與類別套用規格

狀態：**Draft**。第 1 至 3 階段已有第一版實作（category 核心、記憶區塊隔離、
結構化預覽／確認 bridge、GUI 記憶與套用面板）；第 4 階段的 Windows 原生與
AI 新會話驗收尚未執行，使用者確認前不改為 Done。
依據：[共用記憶主規格](shared-memory-spec.md)。驗收案例見
[驗證計畫](memory-validation-plan.md)。

## 範圍與畫面

沿用 pywebview、vanilla TypeScript、既有工具分頁與操作輸出區。
新增「記憶」區及「套用」範圍選擇，不重做設定帳號、更新或技能分享介面。

| 區域 | 第一版內容 |
| --- | --- |
| 同步 | 下載整個資料庫；上傳先預覽再確認 |
| 套用 | 選目標工具，勾選「設定」及／或「獨立技能」，預覽後執行 |
| 技能 | 保留現有清單、分享、取消分享、打包；不混入記憶檔案 |
| 記憶 | 全域／專案位置、入口健康度、Git 變更、啟用／停用、上傳記憶 |
| 專案日誌 | 選取本機專案、顯示鍵值、adopt／release、開啟所在資料夾 |

第一版記憶管理透過「開啟資料夾」交給使用者既有編輯器，不提供
內嵌 Markdown 編輯、逐筆刪除、索引重建、自動記憶匯入或 AI 代寫。
使用者不必另建資料根目錄，所有操作沿用 acg 已設定的 data repository。

## 同步與套用操作

「下載更新」不受工具分頁或類別勾選影響，固定下載整個資料庫。
旁邊常駐顯示：「設定與獨立技能下載後可選擇套用；共用記憶會立即更新。」
成功後重新整理狀態，可按「檢視可套用變更」，不自動 apply。
取消後續套用不回復已下載的 Git 更新，也不回復記憶。

套用目標沿用「全部／Claude／Codex／agy」單一選擇，不新增多選工具
組合。首次開啟套用面板時，設定與獨立技能皆未勾選；空集合停用
預覽與套用按鈕。勾選只保存於本次面板，關閉後重設，避免下次誤用。

預覽按目標、類別列出新增、修改、刪除、遷移、連結變動及略過原因。
顯示真正受影響的檔案與共用連結目標；確認按鈕附工具、類別與變更
數量。空變更僅顯示「已一致」，不建立備份或要求確認。

選擇 settings 時，說明「含規則、指令、MCP 與工具管理的外掛設定」。
選擇 skills 時，說明「僅獨立技能；外掛內附技能隨外掛設定管理」。
既有 share／unshare 是修改資料庫的獨立操作，不自動套用或上傳。

## CLI 類別契約

新增語法只作用於 apply；pull、init、push 與 share 的類別語意不擴張。

```bash
acg apply all --category settings
acg apply codex --category skills
acg apply agy --category all
acg apply claude
```

`--category` 接受 `settings|skills|all`，省略等同 `all`，保留既有 CLI
行為。選項可在工具參數前或後，省略工具仍為 `all`。重複 category、
未知值及多個工具參數在建立暫存或備份前拒絕；更新 usage、guide 與
Bash／PowerShell 補全。GUI 空集合不得轉成 CLI 的預設 all。

工具與類別取交集；`all` 類別等同 settings 加 skills，不含 memory。
本次不新增 category push，也不宣稱可用 Git 只拉取某類別。

## 分類與實際寫入邊界

表內 home 沿用 acg `paths.py` 由 HOME 定義的工具路徑，不能寫死
某個使用者的絕對路徑。本輪不新增 `CODEX_HOME` 等外部工具環境
變數的目標解析；畫面顯示真正路徑，避免誤認已設定其他帳號 home。
分類依目的端管理單位，不依來源資料夾名稱。

| 工具 | settings | skills |
| --- | --- | --- |
| Claude | `CLAUDE.md`、`mcp.json`、`settings.json`、`statusline.sh`、`rules/`、`agents/`、`commands/` | Claude home 的 `skills/` |
| Codex | `AGENTS.md`、`config.toml`、`rules/` | canonical `~/.agents/skills/`，必要的 legacy 遷移與管理標記 |
| agy | CLI home 的 `settings.json`、`mcp_config.json`、`plugins/` | `~/.gemini/config/skills/`、CLI skills 表面及必要鏡像／連結狀態 |

Claude apply 目前不管理 `plugins/`，此分類不新增這項能力。agy 外掛
整體歸 settings，保留 registry 路徑改寫與快取排除；Codex plugin
啟用設定同樣歸 settings，保留既有機器本地設定合併規則。

Codex／agy 的 Claude agents 轉 skill 屬 skills，即使來源是
`claude/agents/`。投影順序沿用現況：agents 轉換 → 工具專屬 skills
→ Claude skills → shared/both → shared/tool，後者覆蓋同名項目。
只套 settings 不執行 agents 轉 skills；只套 skills 仍可讀必要 agents。
Codex rules 的來源 overlay、額外 live rules 保留等語意維持不變。

skills 範圍含 managed manifest、known-unmanaged、遷移標記、agy
Windows mirror ownership 狀態；Codex `.system` 與非管理技能保留。
只套 settings 不建立 canonical skills、不移動 legacy、不改技能
連結、不修剪 orphan、不寫上述標記。agy Unix 已有普通 CLI skills
目錄的既有處理方式保留，這次不順帶改成強制連結。

有意選擇空的技能來源，與「未選 skills」不同：Codex／agy 可依
manifest 刪除過去管理的技能，保留非管理項目；Claude 沿用既有
mirror 行為，來源目錄不存在時不操作 live skills。所有實際刪除
必須出現在預覽與備份；不得因 staged 目錄空就漏掉刪除備份。

未選工具的獨立檔案不改動。但已存在合法共用連結時，選 Codex
settings 可能寫到 Claude 的實體 `CLAUDE.md`；預覽須清楚標出
「此檔案亦由 Claude 使用」及真正目標。沿用專屬 Codex AGENTS
不得穿透共用連結的拒絕規則，不為通過套用而鬆綁安全檢查。

memory 資料與入口由 memory 操作擁有：settings apply 改寫規則
檔時，保留本機原有 acg memory 管理區塊；本機沒有區塊時不因資料
來源有區塊而新增。這項處理只識別完整 acg 標記，不改其他規則。
標記破損或重複時拒絕該次 settings 套用；skills 套用不受其阻擋。
因此 pull／apply 不會順帶啟用或停用本機記憶。這是新增隔離要求，
不是目前 apply 已經驗證的行為。

## 記憶狀態與操作

狀態分開顯示資料與入口：Git clean／dirty／未納管、共用目錄
missing／ok／conflict，以及每個工具的 missing／installed／blocked。
installed 顯示「規則已安裝，請開新會話驗證」，不能寫「AI 已載入」。
Codex override 額外顯示遮蔽原因；CLI 是否安裝和規則是否存在分開。

全域區永遠可檢視。專案區先用原生目錄選擇器選本機 checkout，
後端推導專案根與鍵值，再顯示對應記憶和日誌路徑。未選專案時，
專案操作停用；不把雙擊 GUI 的工作目錄誤當成使用者專案。
不允許輸入任意鍵值來把 A 專案的日誌移進 B 專案。

| 動作 | 確認前預覽 | 成功後 |
| --- | --- | --- |
| 啟用共用記憶 | 索引／連結、三方規則、本機與資料來源修改、remember 設定、衝突 | 刷新狀態，提示開新會話；不提交 |
| 停用共用記憶 | 將移除的管理區塊與連結，說明資料保留、既有日誌不搬回 | 刷新入口；不提交 |
| 同步此專案日誌（adopt） | 專案鍵值、來源／目的地、同名保留名稱、連結與設定變動 | 顯示尚未提交；不自動 push |
| 日誌改存本機（release） | 搬回路徑、Git 刪除差異，以及後續同步對其他機器的影響 | 保留本機內容；不自動 push |
| 上傳記憶 | memory 變更與完整待推送 commit 範圍 | 確認後才提交／推送；沿用範圍守衛 |
| 開啟資料夾 | 不需確認；只接受後端核發的位置識別碼 | 交給 OS 開啟；路徑不存在就提示，不自行建立 |

規則與日誌操作使用獨立預覽，不使用推送來模擬。remember 未安裝
時停用 adopt 並說明原因；已 adopted 的 release 不依賴 plugin 存在。
外部 `data_dir`、未知連結或不安全路徑顯示衝突，沒有「強制覆蓋」。
若只有部分入口可用，完整列出結果；不能因單一入口成功就顯示全部
啟用成功。整組變更中發生錯誤須進入復原流程。

## Bridge 契約

保留現有 API，新增明確方法，不開放任意 shell／CLI 字串。
TypeScript 型別放 `gui/src/bridge.d.ts`，Python API 在
`ai_config/commands/gui.py`；結構化資料不從彩色輸出反向解析。

```typescript
type ToolScope = "all" | "claude" | "codex" | "agy";
type ApplyCategory = "settings" | "skills" | "all";
type MemoryAction = "enable" | "disable" | "adopt" | "release";
type PushScope = ToolScope | "memory";

// 回傳結構見下方欄位定義。
interface MemoryAndApplyApi {
  select_memory_project(): Promise<ProjectSelection>;
  memory_info(projectToken?: string): Promise<MemoryInfo>;
  open_memory_location(locationToken: string): Promise<OperationResult>;
  preview_memory(action: MemoryAction, projectToken?: string): Promise<ChangePreview>;
  confirm_memory(token: string): Promise<OperationResult>;
  preview_apply(tool: ToolScope, category: ApplyCategory): Promise<ChangePreview>;
  confirm_apply(token: string): Promise<OperationResult>;
  cancel_preview(token: string): Promise<OperationResult>;
  preview_push(scope?: PushScope): Promise<PushPreview>;
  confirm_push(scope: PushScope, token: string): Promise<OperationResult>;
}
```

- `ProjectSelection`：`code`、`error`、`output`、`cancelled`、`project_token`、
  `root`、`key`、`stable`。取消選擇器不改當前專案，也不執行操作。
- `MemoryInfo`：`code`、`error`、`output`、資料根與連結狀態、各入口
  狀態／原因、Git changed paths、是否已納管、專案與日誌狀態、允許
  的動作與阻擋原因，以及可開啟位置的 token。無專案時相關欄位為 null。
- `ChangePreview`：`code`、`error`、`output`、`token`、`needs_confirmation`、
  正規化範圍、`changes`、`warnings`；無變更或被阻擋時 token 為空。
  每個 change 包含類別、工具、操作、來源（可空）、目的地、實體目標、
  是否共用、原因；檔案內容差異可用既有輸出區顯示。
- `OperationResult`：保留 `code`／`output`，新增 nullable `error`、
  `backup_path` 與 `recovery_required`。`code=0` 才是成功。
  PushPreview 保留現有欄位，新增同樣 error 契約與完整範圍資訊。

錯誤代碼固定為 `INVALID_ARGUMENT`、`NOT_CONFIGURED`、`BUSY`、
`UNSAFE_PATH`、`CONFLICT`、`STALE_PREVIEW`、`GIT_BLOCKED`、
`IO_ERROR`、`ROLLBACK_FAILED`；補充細節放 output，不把成功輸出當錯誤。

adopt／release 必須帶後端選擇器核發的 project token；enable／disable
不接受 project token。token 僅本視窗有效，專案根／origin 或資料庫
設定變動即失效。後端重驗所有輸入與路徑，不能信任前端已停用按鈕。
處理專案使用明確 Path 參數，不以全程序 `os.chdir()` 切換專案。

新的 GUI 套用只走 preview／confirm；既有 `run("apply", tool)` 要
拒絕並指示重新預覽，避免 bridge 留下免確認通道。CLI apply 維持
既有直接執行行為。`run("push")` 繼續拒絕；記憶也不走通用 run。

## 預覽、鎖與復原

apply／memory 預覽可以建立私有暫存投影，但不得寫 live、data
working tree、Git index 或備份。取消透過 `cancel_preview` 撤銷後端 token 並清理自己的暫存資料。
既有 push 預覽會 gather 到資料庫，不保證完全唯讀；畫面須說明
「已收集本機設定，尚未提交或上傳」，取消保留已收集的差異。
僅 memory push 沒有 gather。後端擴充 memory scope 必須保留兩段
確認及既有所有 Git／秘密掃描守衛。

每個可確認預覽 token 綁定動作、工具／類別、專案、資料根、來源
與目的端 bytes／不存在狀態、連結目標、管理標記及 Git 狀態。
不能只比檔名、mtime 或前端顯示文字；push 也須將完整差異／提交
範圍與實際確認連結。同時只保留一個待確認操作；前端改範圍、
關閉確認框或切資料庫時呼叫 `cancel_preview`，不只丟棄前端 token。
取消僅撤銷相符 token，重複取消可成功且不影響後來產生的預覽；
confirm 已開始執行時回 BUSY，不強制中止操作。後端在新預覽、
其他修改操作及視窗關閉時也撤銷舊預覽並清理暫存；視窗重啟不
恢復 token。取消 push 不回復 gather 差異，不改 Git 暫存狀態。

確認時先取得 GUI 動作鎖與後端 apply 鎖，再重新驗證範圍及指紋。
內容不同回 `STALE_PREVIEW`，不自動替換成新範圍後繼續。token
單次使用，成功、失敗或 stale 後都須重新預覽；BUSY 不消耗 token。
讀取後仍可能被外部 AI 改寫，不能把 acg 鎖說成跨程序交易。

category 必須貫穿 projection、路徑安全檢查、Windows link 預檢、
備份、migration、reconciliation 與 apply。未選類別的格式損壞或
葉節點連結不阻擋已選類別；共同祖先的安全檢查仍不可省略。

所有已選工具完成預檢與投影後才能改 live。備份含真正影響的實體
檔案、連結及 ownership 狀態，依功能區分；失敗回復已選範圍，保留
未選範圍。復原不完整回 `ROLLBACK_FAILED`，列殘留與備份位置。
復原前也要比對操作寫入後的內容，若已有外部修改，不強行覆寫；
保留備份並回報需人工處理。一般 apply 的跨工具復原若尚不足，須
補實作，不以 GUI 包裝宣稱保證。

操作中停用會改變範圍的控制項；不提供強制中止正在搬移／提交的
按鈕。完成或失敗後刷新狀態，錯誤保留在輸出區，不跳出未授權
提權、秘密掃描略過或自動重試推送流程。

## 可用性與交付順序

鍵盤可操作分頁、勾選與確認；dialog 開啟時移入焦點，關閉回原
按鈕；錯誤以文字呈現，不只靠顏色。長路徑可換行或複製，不撐破
視窗。沿用可伸縮版面、rem／em 尺寸與既有樣式，不新增框架。

1. 補 category 核心、memory 區塊隔離與相應測試。
2. 補結構化狀態、專案參數、預覽／確認、復原與 bridge 測試。
3. 接 GUI 記憶與套用操作，補 mock bridge／Playwright。
4. 執行 Windows 原生及 AI 新會話驗收，記錄每項證據後才判斷可交付。

四階段均為待完成；文件整理完成不等於功能完成，也不自動標記 Done。
