# 共用記憶與類別套用驗證計畫

狀態：**Draft**。案例是交付要求，並非通過紀錄。
對應[主規格](shared-memory-spec.md)與[GUI／套用規格](memory-gui-and-apply-spec.md)。

## 證據與放行原則

每個案例記錄 `PASS`、`FAIL`、`NOT_RUN` 或 `BLOCKED`；BLOCKED 須
寫明缺少的 runtime、權限或環境。pytest skip 不能算 PASS。
隔離 HOME 中的檔案測試、GUI mock 測試、原生桌面操作及 AI 新會話
是不同層級的證據，不能互相替代。

目前基線：已有 CLI 隔離測試與 Linux 安裝後測試；本計畫新增的
category／GUI 契約尚未實作，Windows 記憶原生與三方新會話仍未完成。
已有 Windows workflow 不等於這批新增契約已通過 Windows 驗收。

測試使用臨時 data Git repo、合成設定與筆記、測試用 bare remote。
不將真實私人筆記、帳號 home、主機資訊或服務 URL 放進公開 repo、
CI artifact 或截圖。真實 AI 登入在專用測試使用者環境完成，不能
複製正式憑證到 fixture；工具無法隔離 home 時改用拋棄式 OS 使用者
或 VM，不借用正式設定當替代品。

## 平臺與執行方式

| 環境 | 必須覆蓋 | 證據 |
| --- | --- | --- |
| Linux x86_64，Python 3.11／3.12 | 核心、Git、bridge、symlink | pytest 與靜態檢查 |
| Linux ARM64 | 安裝後 acg 記憶與 category smoke | 真正安裝入口、版本、命令與結果 |
| Windows 原生，Python 3.11／3.12 | Junction、路徑、編碼、時間戳、備份／復原 | Windows runner 測試，不能在 WSL 替代 |
| Windows 原生桌面 | onefile GUI、目錄選擇、打開資料夾、操作確認 | 原生 `.exe`、WebView 與畫面證據 |
| Linux Chromium | GUI 互動、版面、錯誤與無障礙基本操作 | Playwright trace／截圖 |
| macOS 發佈 artifact | 記憶 symlink 與 GUI 基本 smoke | 該 OS 原生建置及執行；未測不得宣稱驗證 |
| Claude／Codex／agy 實際 runtime | 全域／專案記憶的新會話讀寫 | 分別記錄 CLI 版本、啟動參數及實際證據 |

沿用 `.github/workflows/python-cli.yml` 的 Linux／Windows 3.11／3.12
矩陣，以及 `gui.yml` 的 pnpm 建置與 Playwright。若只改 bridge 也
應觸發相關 GUI 契約驗證，實作時補齊 workflow path filter。
打包測試使用 `standalone-release.yml` 對應的原生 build，不從 Linux
交叉編譯 Windows。無桌面的 CI 不能代替原生 GUI 人工 smoke。

實作後的基本檢查：

```bash
python -m pytest
ruff check ai_config tests
bash -n install.sh
git diff --check
```

前端在 `gui/` 執行：

```bash
pnpm install --frozen-lockfile
pnpm build
pnpm exec playwright install chromium
pnpm test
```

Linux CI 缺瀏覽器系統套件時沿用 workflow 的 `--with-deps` 安裝方式。
Windows 的 installer、PowerShell 與原生路徑測試須在 Windows 執行；
GUI screenshot 本身不能證明 backend 寫入或 Git 範圍正確。

## 核心與 Git 案例

| ID | 操作／情境 | 必須成立 |
| --- | --- | --- |
| M01 | 無目錄執行 status／path，多次重複 | 不建立檔案、連結、備份或 Git 變更 |
| M02 | 首次／重複 enable，disable，再 enable | 正確安裝且冪等；停用保留全部筆記；管理區塊外文字不變 |
| M03 | Claude／Codex 規則獨立、合法共用連結、override | 各入口分開回報；未知外鏈拒絕；遮蔽不誤報已載入 |
| M04 | 任一工具以原子 rename 替換記憶檔 | 同一目錄連結仍有效，其他讀者看到新 bytes |
| M05 | 同名普通目錄、斷鏈、父目錄外鏈、惡意 reparse、來源越界 | 預檢拒絕，所有外部內容與原入口不變 |
| M06 | agy 同名非管理規則、管理區塊前後有文字 | 前者拒絕；後者只改管理區塊 |
| M07 | enable／disable 各寫入點注入失敗，包含備份失敗 | 備份失敗不開始修改；其他錯誤回復原內容，失敗復原可診斷 |
| M08 | 日誌 adopt／release／再次 adopt、未先 enable 就 adopt | 內容保留、鍵值與目的地一致；adopt 不誤報 AI 入口已啟用 |
| M09 | 日誌同名不同內容、多個 metadata、搬移／連結失敗 | 所有版本有唯一保留名稱；失敗無遺失；復原連結及設定 |
| M10 | remember 不存在、已有外部 data_dir、release 時 plugin 不存在 | adopt 拒絕不接管；合法已採用日誌仍可 release |
| G01 | pull 同時更新設定、skills、memory | live 設定／skills 不變，memory 新 bytes 立即可讀；沒有自動 apply |
| G02 | tracked memory dirty、混合 dirty、新增 untracked 筆記 | 前兩者拒絕並正確列範圍；新檔不被覆蓋，遠端撞路徑時 Git 拒絕 |
| G03 | ahead、分歧、merge／rebase 中、缺 upstream | 拒絕拉取，不 stash、不合併、不覆蓋使用者變更 |
| G04 | push memory，外部 staged／其他類別 dirty／跨範圍 ahead | 不混入其他變更，不漏掉待推送歷史；沿用守衛拒絕 |
| G05 | push all 無 memory／有 memory／缺失原本存在的 memory 根 | 無 memory 舊資料庫可用；有 memory 仍 gather 設定；整根缺失不推送刪除 |
| G06 | 使用者刪單一筆記，或 release 日誌後推送 | 預覽顯示真實刪除；確認才提交；另一 checkout pull 後結果相符 |
| G07 | Git 與 Google Drive provider | 同一範圍規則；bundle 不含未提交內容，已提交記憶須納入完整範圍檢視 |
| G08 | enable 同時修改來源規則與 memory，接著 push memory | 顯示跨範圍阻擋與完整保存途徑，不自動把設定放進記憶提交 |

## 類別隔離與預覽案例

測試三工具與 `settings|skills|all` 的九種組合，另驗證工具 all。
對未選範圍比較 bytes、存在狀態、mtime、連結目標及管理標記，
不能只檢查某一個測試檔；備份內也不應混入未選範圍。

| ID | 情境 | 必須成立 |
| --- | --- | --- |
| C01 | CLI 省略 category、前後位置、未知／重複值 | 省略等同 all；非法參數在寫入前拒絕 |
| C02 | settings only，有 legacy skills／orphan／異常技能葉節點 | 不遷移、修剪、建立技能連結或標記；未選葉節點不阻擋 |
| C03 | skills only，設定 JSON 損壞／規則標記破損 | skills 可套用且設定不變；共用祖先不安全仍拒絕 |
| C04 | agents 轉 skills 與五層同名投影 | 優先序不變，技能內容及 frontmatter 正確 |
| C05 | 已選 skills 空來源，對比未選 skills | 按工具既有清理契約操作，Codex／agy 保留非管理技能與 `.system` |
| C06 | agy plugin 含 SKILL.md，Codex plugin 本機設定 | 分類不拆散外掛；本機設定依既有合併契約保留 |
| C07 | Codex settings 寫穿合法共用 AGENTS 連結 | 預覽、真正寫入與備份目標一致，揭露 Claude 影響；專屬來源防護保留 |
| C08 | 本機 memory 區塊有／無，資料來源有／無 | 四種組合 apply 後維持本機入口狀態，其他規則正常更新 |
| C09 | 任一選取工具 staging 失敗、多工具寫入中失敗 | 前者完全不寫 live；後者回復已改範圍；若外部再修改，保留內容並報需人工復原 |
| C10 | 預覽後檔案 bytes 改變但 mtime 相同；改連結／manifest／origin | token 失效，沒有未檢視的寫入；不能只比 mtime 或文字摘要 |
| C11 | 取消 apply／memory 預覽，空差異，重放 token，BUSY | 取消／空差異無 managed 寫入；取消後直接 confirm 必須拒絕；token 單次有效；BUSY 可重試原 token |
| C12 | 備份／復原含 legacy、canonical、共用規則與鏡像 ownership | 與實際變更範圍相符；復原不改未選類別 |

## GUI 案例

| ID | 情境 | 必須成立 |
| --- | --- | --- |
| U01 | 初開套用、勾選切換、工具切換、全部取消 | 初始不選，空集合不能執行，確認顯示正確工具／類別 |
| U02 | 在任一工具分頁下載、取消後續套用 | 固定下載全 repo，說明記憶立即更新，取消不謊稱回復下載 |
| U03 | 未設定資料庫、未選專案、原生選擇器取消 | 明確缺少狀態；專案動作不執行；不使用 GUI cwd |
| U04 | 無遠端、不同路徑相同遠端、可能碰撞的鍵值 | 正確推導、顯示 fallback／鍵值／目的地，不宣稱全球唯一 |
| U05 | 四種 memory 變更與記憶 push | 分別預覽、確認、備份／結果；沒有繞過確認的 run 通道 |
| U06 | push 預覽 gather 後取消 | UI 說明已收集差異；未提交、未推送、變更保留未暫存 |
| U07 | status installed、Codex override、缺工具、未知 agy 載入 | 安裝狀態不冒充 runtime 驗收成功 |
| U08 | 預覽過期、後端 BUSY、I/O 或復原失敗 | 顯示可讀原因；復原失敗保留備份位置，不自動重試寫入 |
| U09 | 鍵盤、dialog 焦點、窄視窗、長路徑及多筆變更 | 操作可達，焦點回復、無橫向撐破；錯誤不只靠顏色 |
| U10 | 偽造 scope／category／project token／location token | backend 拒絕，不能任意開路徑、執行 CLI 或改其他專案 |

## Windows 必測細節

W01：普通權限在本機 NTFS 建立真實 directory Junction，檢查直接
目標與解析後 identity；enable、原子替換、adopt、release、disable
皆須實際執行。mock 或因 symlink 特權不足跳過不算 Junction 通過。

W02：資料根與專案路徑含空白、繁體中文、不同大小寫及尾端分隔符。
備份 manifest 可還原，同名來源不碰撞；CLI 相對路徑以 `/` 顯示，
PowerShell 5.1／7 的啟動與文字輸出不出現亂碼。

W03：鎖定目標檔案、拒絕存取、斷裂 Junction、巢狀 reparse、外部
連結及 link 建立失敗。驗證錯誤復原與 ownership，不改用任意副本
來通過測試。UNC／非 NTFS 若不支援，明確拒絕且不部分改寫；不
把它們列為第一版支援路徑。長路徑政策另外記錄，不推論全面支援。

W04：每個 category 在 Windows 跑 C01–C12，尤其 agy mirror 與
canonical／legacy 狀態。不可只在 Linux 測分類、Windows 只測連結。

W05：原生 onefile `.exe` 從檔案總管雙擊與終端機啟動，版本與 build
commit 可辨識；GUI 正常、原生選擇器與開資料夾可用，終端機輸出
正常。若 WebView 不足則記錄 BLOCKED，不把 API 測試代替桌面測試。

## AI 新會話：讀取、寫入與互通

每個 runtime 分開執行以下流程；記錄實際二進位位置、版本、模型、
home 隔離方法與啟動參數。只用可支援的正式新會話模式，不復用
resume／歷史對話，不在 prompt 貼入規則或記憶內容來假裝自動載入。
測試 runtime home 必須與 acg 操作路徑一致；另做不同 CODEX_HOME
且無共用連結的負對照，確認狀態不宣稱已設定那個額外 home。

1. 在隔離 data repo 放入隨機測試標記：全域索引指向一份 topics
   文件，專案索引指向另一份 topics 文件；預期值只存在文件中。
   測試 prompt 只詢問測試知識的名稱，不包含值或記憶檔案位置。
2. 執行 enable，完全結束再開新 AI 會話，詢問全域與專案測試知識。
   必須得到正確標記，並收集檔案讀取 trace／來源證據；只回答「已
   載入」不算。兩層索引和按需 topics 都要覆蓋。
3. 做沒有記憶入口的負對照：新環境、全新標記、同樣問題。若仍能
   回答，先調查其他提示／記憶來源或快取污染，不能直接算成功。
4. 一般詢問但不說「記住」，檢查共用 memory 無寫入。接著明確
   要求記住一條合成的跨專案偏好與一條專案決定；驗證全域／專案
   目的地正確，有日期、來源、範圍，Git diff 可見且沒有憑證。
5. 預先確認測試知識不在工具自動記憶內。明確記住後檢查該知識沒有
   被另存成自動記憶副本；工具其他背景 metadata 變動不當成失敗。
6. 關閉寫入者，在另外兩個工具的新會話詢問相同測試知識；執行
   Claude→Codex、Claude→agy、Codex→Claude、Codex→agy、
   agy→Claude、agy→Codex 六個方向，每次用新的隨機值。
7. 第二個隔離使用者／機器以不同 checkout 路徑、相同 origin
   clone 資料，pull 後確認同一專案鍵值與知識可讀；來源明確提交
   與推送在測試用 remote 內，不使用正式資料 remote。
8. disable 後再開新會話，搭配新的負對照標記確認不靠已移除入口
   讀取；另測不存在索引、沒有專案遠端、Codex override 遮蔽。

規則檔存在但讀取測試失敗，該 runtime 判定 FAIL。agy 必須特別
確認實際 CLI 支援的全域規則入口；若候選 rules 路徑不會自動載入，
修正 adapter 與規格後重跑，不以 IDE 文件或手動指定讀檔代替。
未安裝 runtime、無法登入或無法建立隔離會話，記錄 BLOCKED，不能
宣稱三方互通已完成。對受測版本的結論不自動延伸到所有未測版本。

## 紀錄格式與交付條件

每次驗證保存以下欄位；正式報告可放 `docs/validation/`，原始含
敏感環境資訊的 log 先去識別化，不自動加入公開 repository。

```text
case_id:
status: PASS | FAIL | NOT_RUN | BLOCKED
tested_at:
acg_commit:
acg_version:
os_arch_python:
runtime_version:        # 非 AI 案例可為 n/a
artifact_or_entrypoint: # 路徑去識別化；打包檔另記 checksum
steps:
expected:
actual:
evidence:              # log、diff、trace、截圖或 CI run 的可查位置
blocker_or_followup:
```

交付 GUI／category 前必須通過 M／G／C／U 的相關案例、Linux 與
Windows 3.11／3.12 自動檢查，以及 Windows W01–W05。宣稱三方
AI 共用記憶可用前，另需三個 runtime 的正負對照、寫入歸屬與六向
互通證據。若只完成部分，逐項列出可用與未驗證範圍，不以整體
「測試通過」掩蓋缺項。使用者確認前，規格狀態仍維持 Draft。
