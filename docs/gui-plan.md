# acg GUI 規劃

狀態：**Draft**。只有使用者明確確認後才改為 Done。

## 目標與規格入口

讓使用者以圖形介面設定資料庫、檢查狀態、下載、套用與上傳，並
分開管理技能與記憶。沿用既有介面，新增功能依下列文件實作：

- [共用記憶規格](shared-memory-spec.md)：儲存、Git、規則與日誌契約。
- [GUI 記憶與類別套用](memory-gui-and-apply-spec.md)：畫面、CLI 選項、
  bridge、預覽、確認與復原；是本輪 GUI 增量的實作依據。
- [驗證計畫](memory-validation-plan.md)：自動測試、Windows 原生與
  三個 AI 新會話的證據要求。

本文件是 GUI 架構總覽，不取代上述具體契約。主機設定組另見
[host profiles](host-profiles-spec.md)，尚未實作且不納入本輪 GUI。

## 現有架構

| 層 | 選擇與位置 |
| --- | --- |
| 視窗 | pywebview，optional extra `ai-config[gui]` |
| 前端 | Vite、vanilla TypeScript、pnpm，`gui/src/main.ts` 與 `style.css` |
| Bridge | `gui/src/bridge.d.ts`；Python `ai_config/commands/gui.py` 的 GuiApi |
| CLI 共用 | 部分操作 in-process 呼叫 CLI 並擷取輸出，部分回傳結構化資料 |
| 打包 | PyInstaller 使用 `ai_config/gui_assets/`，先 pnpm build；各 OS 原生建置 |
| 測試 | Python API 契約、mock bridge／Playwright、另加原生桌面 smoke |

既有 stdout／stderr 顯示保留；新增狀態與變更預覽用結構化資料，
不以行首圖示或翻譯後文字決定程式行為。

## 現況與增量

| 功能 | 現況 | 本輪增量 |
| --- | --- | --- |
| 狀態與工具分頁 | 已有 | 套用類別與記憶各自呈現 |
| 下載 | 已有 pull | 明示全 repo 更新及記憶立即生效 |
| 套用 | 已有按工具 apply | settings／skills 範圍、預覽／確認、隔離與復原 |
| 上傳 | 已有 preview_push／confirm_push token，重新檢視後才確認 | 新增 memory scope，完整範圍與過期檢查 |
| 初次設定 | 已有 Git／Google Drive 設定與登入流程 | 沿用，不列成未來雛形 |
| 技能 | 已有清單、分享、取消分享、打包 | 保持獨立，不混入 memory |
| 記憶 | 尚無 GUI 操作 | 入口狀態、開資料夾、enable／disable、日誌 adopt／release、push |
| 更新 | 已有檢查與執行更新 | 沿用版本與 build commit 資訊 |

通用 `run` 使用命令白名單；目前已拒絕 raw push。新規格亦要求
raw apply 改走預覽流程。GUI 不暴露任意 shell、秘密掃描略過或
強制覆蓋選項。取消 push 預覽可能保留 gather 差異，不能宣稱所有
預覽都完全唯讀。

## 交付驗證

前端 `pnpm build` 與 `pnpm test`、Python API 與核心測試都需通過。
Linux 的瀏覽器測試不替代 Windows WebView／onefile 實測；CLI
記憶檔案測試不替代 AI 新會話載入。具體案例與放行條件統一記在
[驗證計畫](memory-validation-plan.md)，不在此另維護第二份清單。
