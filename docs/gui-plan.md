# acg GUI 規劃(Draft)

> 狀態:**Draft** — 尚未經使用者確認為 Done。

## 目標

給「會用 AI CLI 但不開終端機」的非工程師一個雙擊就能用的圖形介面,
核心動作:看同步狀態 → 一鍵套用 → 一鍵下載/上傳。
最終以 PyInstaller 打包為各平臺獨立執行檔(Windows 為 `.exe`)。

## 技術選型(已與使用者確認)

| 層 | 選擇 | 理由 |
|---|---|---|
| 視窗殼 | pywebview(optional extra `ai-config[gui]`) | 用系統 WebView 渲染,體積小、外觀現代 |
| 前端 | Vite + vanilla **TypeScript**(pnpm) | 型別安全的 bridge 契約;暫不引框架,之後要升 Preact/Vue 幾乎免費 |
| 後端橋接 | pywebview `js_api` → in-process 呼叫 `ai_config.__main__.main()` 並攔截 stdout/stderr | 雛形階段不重構 1750 行 CLI;輸出轉結構化留待下一階段 |
| 打包 | 沿用 PyInstaller;CI 先 `pnpm build` 再打包 | 前端產物是純靜態檔,當資源塞進執行檔 |

## 目錄結構

```
ai-config/
├─ gui/                     # 前端源碼(Vite + TS)
│  ├─ package.json
│  ├─ tsconfig.json
│  ├─ vite.config.ts        # outDir → ../ai_config/gui_assets
│  ├─ index.html
│  └─ src/
│     ├─ main.ts            # UI 邏輯
│     ├─ bridge.d.ts        # Python js_api 的型別契約(前後端唯一介面)
│     └─ style.css
├─ ai_config/
│  ├─ gui.py                # `acg gui` 進入點:pywebview 視窗 + Api 類別
│  └─ gui_assets/           # pnpm build 產物(git-ignore,CI/本地建置產生)
```

## 命令範圍(雛形)

GUI 只暴露安全、非互動的動作,白名單制:

| GUI 動作 | 對應 CLI | 互動處理 |
|---|---|---|
| 檢查狀態 | `status [tool]` | 無提示,直接跑 |
| 套用設定 | `apply [tool]` | 無提示,直接跑 |
| 下載更新 | `pull [tool]` | 無提示,直接跑 |
| 上傳變更 | `push [tool]` | GUI 先跳自己的確認框,確認後以預填 `y` 的 stdin 執行 |

`reset` / `deploy` / `setup` 留在 CLI(破壞性或多步互動),之後再逐步 GUI 化。

## 輸出呈現

CLI 輸出在非 TTY 下自動無色(`console.py` 的 `_COLOR`),後端再保險 strip ANSI。
前端依行首符號上色:`✓` 綠、`⚠` 黃、`✗` 紅、`═══` 為區段標題。

## 後續階段(不在本次雛形)

1. **首次設定引導** — setup 流程 GUI 化:貼 token / OAuth,取代 SSH key。非工程師的最大門檻。
2. **結構化狀態 API** — 把 `show_status` 重構為回傳資料的函式,GUI 直接畫表格而非解析文字。
3. **打包與簽章** — CI 加 node/pnpm 步驟;Windows 評估 code signing 憑證或 Inno Setup 安裝檔以緩解 SmartScreen。
4. **push 兩段式** — 先取得 diff 摘要顯示,確認後才真正 commit/push。

## 驗收(雛形)

- `pip install -e .[gui]` 後 `acg gui` 能開視窗,四個動作可用。
- `pnpm build` 通過 TS 檢查;`pytest` 全綠(含 gui Api 層的攔截測試)。
- 無 display 的環境(SSH)至少 Api 層測試可驗證。
