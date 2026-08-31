# Google Drive 同步後端規格(Draft)

> 狀態:**Draft** — 尚未經使用者確認為 Done。
> 分工:本規格由 Claude 撰寫並負責驗收;實作交由其他 AI。
> 驗收標準見文末,實作者請逐項自檢後再交付。

## 0. 目標與非目標

**目標**:讓沒有 GitHub 帳號的使用者(GUI 的目標族群)用 Google 帳號登入,
把 acg 的資料儲存庫同步到 Google Drive 的 `appDataFolder`,跨裝置使用。

**核心設計原則(不可違反)**:本機資料儲存庫**仍然是完整的 Git repo**。
Drive 只是傳輸層(取代 `git push`/`git fetch` 的遠端),版本歷史、diff 審查、
secret 掃描、ff-only 保護全部保留。禁止退化成「上傳/下載 ZIP 蓋掉對方」。

**非目標(v1 不做)**:
- 多人共用同一份設定(appDataFolder 是單一 Google 帳號私有的)
- 合併衝突解決(維持 ff-only;diverged 時要求使用者先 pull)
- Google Drive 桌面版資料夾方案
- 增量 bundle(設定檔僅數 MB,每次全量即可)

## 1. 架構

### 1.1 Provider 抽象

`~/.config/ai-config/config.json` 新增欄位:

```json
{ "data_repo": "...", "remote_provider": "git" }
```

- `"git"`(預設,欄位缺省視同 git):現行行為,零改變。
- `"gdrive"`:pull/push 的遠端傳輸改走 Drive;本機 repo 可以沒有 git remote。

新模組 `ai_config/gdrive.py`(平鋪,不開子套件——與現有架構一致),
只依賴標準庫(`urllib.request`、`json`、`secrets`、`hashlib`、`http.server`),
**不得引入 google-api-python-client 等重依賴**(PyInstaller 體積與供應鏈考量)。

### 1.2 Drive 端資料佈局(appDataFolder)

| 檔名 | 內容 |
|---|---|
| `repo.bundle` | `git bundle create repo.bundle main` 的全量 bundle,每次 push 以 `files.update` 原檔覆寫(同一 fileId,不累積新檔;舊版本交給 Drive 自動修訂清理) |
| `head.json` | `{"commit": "<sha>", "updated_at": "<iso8601>", "device": "<hostname>", "format": 1}` |

### 1.3 同步演算法

**pull(gdrive provider 時取代 `_pull_preflight` 的 upstream/fetch 部分)**:
1. 下載 `head.json`;不存在 → 遠端為空,提示先 push。
2. `head.commit` 等於本機 `main` HEAD → 已是最新。
3. 下載 `repo.bundle` 到暫存檔 → `git bundle verify` → `git fetch <bundle> main`。
4. 沿用現行 ff-only 檢查:`git merge --ff-only FETCH_HEAD`;
   無法 ff → 報「本機有未上傳的提交,先 push 或手動處理」,不得強制。
5. 現行的 uncommitted-changes / in-progress-operation 前置檢查照舊執行。

**push(gdrive provider 時取代 `_commit_and_push` 最後的 `git push` 步驟)**:
1. 現行守衛流程**全部保留**:init 收集 → staged diff 審查 → secret 掃描 →
   確認 → commit。只有「送出去」這一步換掉。
2. 送出前:下載 `head.json`,其 `commit` 必須是本機 HEAD 的祖先
   (`git merge-base --is-ancestor`);不是 → 報 diverged,要求先 pull,中止。
3. `git bundle create` → 記下 `repo.bundle` 目前的 `headRevisionId` →
   `files.update` 上傳 → 上傳後重讀 `headRevisionId` 確認是自己這次的修訂 →
   更新 `head.json`。
4. 已知限制(要寫進 docstring):Drive 無原子 CAS,第 3 步只能縮小競態窗口。
   單帳號多裝置場景可接受;`head.json` 的 `device` 欄位供事後診斷。

### 1.4 OAuth(installed app + PKCE)

- Scope 僅 `https://www.googleapis.com/auth/drive.appdata`,不得多要。
- 流程:系統瀏覽器 + loopback redirect(`http://127.0.0.1:<隨機port>`)+ PKCE
  (S256)。
- **client secret 必須附上**(實測修正):Google 的 Desktop 類型 client 即使走
  PKCE,token 交換仍強制要求 client_secret,否則回 400。官方文件明言桌面應用的
  secret「並非機密」,gcloud/rclone 皆內嵌;PKCE 仍保留作為防攔截層。
- Client ID/secret 來源順序:環境變數 `AI_CONFIG_GDRIVE_CLIENT_ID` /
  `AI_CONFIG_GDRIVE_CLIENT_SECRET` → build 時注入的常數(release workflow 從
  GitHub secret 注入;公開 repo 原始碼中兩個常數皆為空字串,client ID 為空時報
  「此建置未包含 Google 登入,請設定環境變數」)。
- Token 存於 `~/.config/ai-config/gdrive_token.json`,權限 0600。
  **絕不放進資料儲存庫**;同時把 `gdrive_token.json` 加進 `EXCLUDED_FILES`
  防呆。refresh 失敗(revoked/expired)→ 清楚提示重新登入,不得靜默重試迴圈。
- 403/429 → exponential backoff(上限 3 次,含 jitter),仍失敗則報錯退出。

### 1.5 Setup 驗證(建立→讀回→刪除)

`acg setup --provider gdrive`(及 GUI)在保存設定前必須:
1. 完成 OAuth 登入。
2. 在 appDataFolder 建立隨機名稱測試檔(內容為隨機 bytes)→ 讀回並比對 →
   永久刪除(`files.delete`)→ 再列舉確認已消失。
3. 全部成功才寫入 `config.json`(`remote_provider: "gdrive"`);
   本機 repo 不存在時 `git init` + 建立 `claude/ codex/ agy/` 骨架
   (語意等同現行 git provider 首次 setup 後的空骨架)。

## 2. 介面變更

### 2.1 CLI

- `acg setup --provider <git|gdrive>`;預設 git,完全向後相容。
- gdrive provider 下 `pull`/`push` 走上述流程;`status` 不變(純本機比對)。
- `acg setup` 互動模式加一個選擇題(Git URL / Google Drive)。

### 2.2 GUI(`gui/` 前端 + `ai_config/gui.py`)

- 首次設定卡片改為兩個 radio 選項:
  - 「Git 儲存庫」:現行表單(URL + 目錄)。
  - 「Google Drive」:一顆「用 Google 帳號登入」按鈕 + 文案:
    「設定會存在你 Google 帳號的隱藏應用程式空間,只有這臺程式能存取;
    適合自己的多臺電腦同步,無法分享給其他人。」
- 橋接新增 `setup_gdrive()`:觸發 OAuth(開系統瀏覽器)→ 驗證 → 回報結果。
  GUI 端顯示「已開啟瀏覽器,請完成登入…」等待狀態。

## 3. 實作邊界(給實作者)

- 觸點:`setup.py`(provider 參數與驗證)、`sync_cmd.py` 的 `_pull_preflight`/
  `do_sync`(provider 分流)、`push_cmd.py` 的 `_commit_and_push`(送出步驟分流)
  與 `_push_snapshot`/`_remote_is_read_only`(gdrive 下不適用,需繞過而非硬跑)、
  `config.py`(新欄位)、`gui.py` + `gui/src/`(表單)、新檔 `ai_config/gdrive.py`。
- **不得改動** git provider 的任何現行行為;350 個既有測試必須全綠。
- 新增測試:mock HTTP 層(不打真網路),覆蓋:pull 空遠端/最新/可 ff/diverged、
  push 祖先檢查通過與失敗、revisionId 不符、token refresh 失敗、setup
  建立→讀回→刪除全流程、`EXCLUDED_FILES` 含 token 檔。CI 不需要真帳號。
- 風格:Python 3.11+、ruff 過、import 分組照 repo 慣例、註解寫 WHY。

## 4. 外部前置(使用者/維護者作業,不在程式內)

1. GCP 建專案 → 啟用 Drive API → OAuth consent screen → Desktop client ID。
2. Testing 模式:100 名測試者、授權 7 天失效;對外發佈前切 In production
   (drive.appdata 為非敏感 scope,通常免品牌驗證,但以 Google 審查為準)。
3. Release workflow 注入 client ID(GitHub secret)。

## 5. 驗收標準(Claude 驗收時逐項檢查)

- [ ] `remote_provider` 缺省/為 git 時,全部現行測試與行為零改變
- [ ] gdrive pull:空遠端、已最新、可 ff、diverged 四情境行為如 §1.3
- [ ] gdrive push:守衛流程完整保留;祖先檢查;revisionId 複查;head.json 更新
- [ ] OAuth:僅 drive.appdata scope;PKCE;token 檔 0600 且列入排除;
      refresh 失效有清楚指引
- [ ] setup 驗證:建立→讀回→刪除→確認消失,失敗不寫 config
- [ ] GUI 雙選項表單可用;Drive 文案含單帳號限制說明
- [ ] 無新第三方執行期依賴;`pnpm build`、`ruff`、全套 pytest 綠
- [ ] 秘密掃描:repo 內不得出現任何 client secret / token;client ID 常數為空
