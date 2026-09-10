# 本機日誌的專案入口驗收

狀態：**Draft**。基底為 `02b9f10`，修正留在工作目錄，未提交或發佈。

## 行為

- `memory enable` 安裝本機 Claude SessionStart／UserPromptSubmit hook，
  GUI 預覽列出 settings.json 變更並納入備份與指紋。
- remember 完成遷移後，缺少的 `.remember` 或只剩有效遷移通知的目錄
  會成為實際日誌入口；不改變 local／adopted 同步狀態。
- 未搬完的內容、錯誤通知與外部連結保留；建立入口失敗時還原通知目錄。
- `memory disable` 移除自己管理的 hook，其他 hooks、日誌及入口保留。
- init／status 排除 acg 的本機 hook，settings apply 保留目的端 hook。
- hook 以 command／args 直接啟動，不經 shell；stdin 僅解析專案位置，
  不讀取對話 transcript，也不執行記憶摘要或上傳。

## 驗證

- 最終完整 Python 回歸：**717 passed，10 skipped**（167.73 秒）。
  ruff、`bash -n install.sh`、`git diff --check` 均通過。
- 新增 11 項回歸：本機入口、冪等、通知／內容／外部連結衝突、連結及
  Git exclude 寫入失敗復原、啟停、設定同步隔離、實際 CLI hook 呼叫。
- Linux ARM64 隔離環境使用本次來源：實際執行註冊的 command／args，
  入口可讀、重複執行成功、維持 local，停用後仍可讀日誌。
- 隔離測試沒有修改該機正式安裝、記憶資料或 Claude 會話。
- Windows 原生 hook／Junction 及真實 Claude 新會話仍待實測。
