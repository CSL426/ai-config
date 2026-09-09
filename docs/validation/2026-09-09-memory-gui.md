# 記憶管理 GUI 與安裝驗收

狀態：**Draft**，待使用者確認。基底為 `ceadcb4` 的工作目錄，
版本 `1.0.38`；本次沒有提交或發佈。

## 已驗證

- 首頁獨立呈現記憶管理與技能管理；記憶頁區分共用位置摘要、
  工具讀取入口、專案記憶與日誌，以及可展開的完整路徑。
- Esc 先關閉下拉選單／設定，再取消預覽或返回上一層；取消須
  撤銷後端 token。執行中、輸入法組字及長按不應誤觸返回。
- 自訂下拉選單支援鍵盤選取、搜尋、停用及動態選項同步；捲軸
  沿用暖色介面。使用 320／375／414／768／880 寬度檢查記憶頁，
  以及 640 × 480 檢查既有技能／設定操作。
- `pnpm build` 與 `pnpm test`：**34 passed**。瀏覽器使用合成 bridge
  資料；截圖由 `gui/tests/navigation.spec.ts`、`select.spec.ts` 產生至
  Playwright 的 `test-results/`，不包含私人設定或筆記。
- 本次接續驗收重新建置 GUI，34 項瀏覽器測試全數通過（8.5 秒），
  並檢視新產生的 320 寬度截圖，確認「記憶管理」標題完整顯示。
  本機 Python 載入此 checkout，GUI 資源路徑指向本次建置產物。
- 套用預覽／確認與失敗復原：**17 passed**。測試不再保留無作用的
  Windows skip 標記；是否通過 Windows 原生環境仍待 CI 證據。
- 最終 Linux、Python 3.12.0 完整 `python -m pytest`：**681 passed，
  10 skipped**（153.48 秒），已包含本次新增的 11 項帳號回歸。
  帳號測試合計 **57 passed**；略過項目不計為驗收通過。
- `ruff check ai_config tests`、`bash -n install.sh`、`git diff --check`
  均通過。
- 前次驗收紀錄：Linux ARM64、Python 3.12.3 的隔離 venv 安裝 `acg 1.0.38`：
  **137 項後端 pytest、10 項 CLI smoke 通過**。涵蓋記憶啟停冪等、
  原子換檔與 settings／skills 範圍隔離；未更動既有安裝或私人 data。
  此安裝證據來自 GUI／帳號最終調整前的來源快照，不能代替新介面的
  原生桌面驗收。

## 驗收發現後的修正

- credential helper 僅接受 HTTPS GitHub 要求；其他主機不讀取或輸出
  token。Git shell helper 的路徑使用安全引號，避免特殊字元展開。
- repository 綁定以 config lock 原子更新；首次綁定保存原 helper，
  重綁保留備份，解綁還原。備份損壞或操作失敗時保留原設定。
- 列舉帳號不再因 Git 已可推送而略過；API 帳號權限與 Git 實際
  推送能力分開。登入後必須通過 Git 驗證才顯示可推送。
- 登入失敗仍嘗試還原原本的 gh 作用中帳號；還原失敗即回報錯誤，
  不繼續綁定或顯示登入成功。Git 推送狀態未知時保留未知，不能用
  API 拒絕結果代替 Git 驗證。
- SSH 推送成功不再宣稱使用綁定帳號；綁定前檢查實際 push URL，
  對無法使用 GitHub HTTPS helper 的遠端拒絕綁定並保留原設定。
- agy 首次套用先建立目標目錄及檔案，再建立 Junction；shadow
  預覽的目標預建不會寫入 live。新增父目錄納入失敗復原。

## 尚未驗證

- Windows 原生桌面、Junction 實際執行及完整 category 矩陣。
  Linux 上的 Junction shim 只驗證寫入順序與復原契約。
- macOS 原生 artifact。
- Claude、Codex、Antigravity 新會話的真實自動讀取、寫入及六向互通。
- 真實 GitHub 憑證與遠端寫入。帳號回歸只使用假 token、臨時 repo
  與 mock，不以 API 權限成功冒充 Git 已可推送。

以上缺項保留為 NOT_RUN；本報告不宣稱三方 AI 或所有桌面平臺
已完成整體驗收。
