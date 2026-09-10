# 全域共用記憶規格

狀態：**Draft**。本文件定義產品契約，並不表示功能已全部完成。
只有使用者明確確認後才改為 Done。

## 文件範圍與目前狀態

| 項目 | 目前狀態 |
| --- | --- |
| CLI 記憶狀態、啟用、停用、路徑、推送、日誌 adopt／release | 已有實作與隔離測試 |
| Linux 安裝後 CLI 測試 | 已有驗證；不等於 AI 新會話載入驗證 |
| GUI 記憶管理、設定／skills 類別套用 | 待實作，依[介面與套用規格](memory-gui-and-apply-spec.md) |
| Windows 原生與三個 AI 新會話驗收 | 尚未完成，依[驗證計畫](memory-validation-plan.md) |

本文件是儲存與生命週期的依據；GUI、類別與 API 以介面規格為依據，
測試證據與放行條件以驗證計畫為依據。[GUI 總覽](gui-plan.md)只描述
整體架構。[主機設定組](host-profiles-spec.md)是獨立 Draft，不是本次前置需求。

## 固定的產品決定

同一套 acg、同一個私人 data Git repository，分資料夾管理設定、
skills 與 memory。不新增 `~/.ai-memory`，不另建記憶 repository，
不遷移既有 skill 結構。公開工具 repository 不保存私人記憶。

設定與獨立 skills 可以先拉取，再選擇是否套用及目標工具。
記憶採直接連到資料庫的設計：**pull 成功後，記憶立即更新**，沒有
第二份記憶副本或 memory apply。更新可能改變 AI 使用的知識與指示，
GUI 必須明示這個影響，不能把記憶描述成「下載但尚未套用」。

Claude Code、Codex、Antigravity CLI（agy）讀同一份記憶。第一版
記憶啟用／停用以本機共用入口整組處理，不提供逐工具開關；逐工具
狀態仍分別顯示。套用畫面的工具選擇只作用於設定與獨立 skills。

只有使用者明確要求「記住」時，才把已確認知識寫入共用記憶。
不合併聊天紀錄，不自動匯入各工具的自動記憶。這是模型指示，
不是 acg 能強制執行的檔案存取限制。

## 儲存與連結

```text
<data-repo>/                    同一個私人 Git repository
├── .git/
├── claude/
│   ├── skills/                 既有 Claude skills
│   └── shared/                 既有跨工具 skills
├── codex/
├── agy/
└── memory/
    ├── MEMORY.md               全域索引與知識
    ├── topics/                 全域主題
    ├── .gitignore              排除本機 journal/
    ├── journal/                remember 本機路徑與連結，不進 Git
    └── projects/
        └── <owner>--<repo>/
            ├── MEMORY.md       專案索引
            ├── topics/         專案主題
            └── journal/        明確 adopt 後的日誌，進 Git

~/.claude/shared-memory -> <data-repo>/memory/
```

三個工具使用同一個相容路徑，不要求安裝 Claude 程式才能使用該目錄。
本機只建共用目錄連結，Git 保存普通資料檔案，不保存機器絕對連結。
Linux/macOS 用 symlink；Windows 用 Junction。連結建立失敗就回報
失敗並復原；第一版沒有自動複製或改成硬連結的替代模式。

資料 repository 不應再由其他檔案同步軟體搬動 working tree 與 `.git/`，
避免繞過 Git 的同步邊界；acg 不宣稱能偵測所有外部同步軟體。
Google Drive provider 傳送 Git bundle，包含已提交歷史；未提交內容
不在 bundle 中，但已提交、尚未推送的記憶仍可能包含在待傳送歷史內。

專案鍵值由 `origin` 最後兩段 `owner/repo` 去掉 `.git` 並依程式規則
正規化為 `<owner>--<repo>`。同一遠端的不同 checkout 共用專案記憶。
沒有遠端時退回目錄名稱，顯示「依本機目錄名稱，跨機器一致性未保證」。
這不是全球唯一識別碼；不同 Git host 或巢狀 namespace 可能碰撞，
不得宣稱同名一定是同一專案。GUI 在日誌搬移前顯示鍵值與目的地，
若使用者發現碰撞應停止操作；自動偵測碰撞與鍵值遷移另案處理。

專案裡的 `.remember/` 是給人看的入口：adopt 之後它是指向共用日誌的
連結，release 之後指向本機日誌，兩者都是同一份資料。這條連結寫進專案
的 `.git/info/exclude`，不進專案的版本控制。`.remember` 若已是指向別處
的連結，acg 不動它，只回報；已經搬過、只剩通知的舊目錄，再跑一次 adopt
就補上連結。

## CLI 契約

| 指令 | 行為 |
| --- | --- |
| `acg memory status` | 唯讀顯示入口、連結、Git 變更與目前專案日誌狀態 |
| `acg memory path [--global\|--project]` | 印出全域或目前專案路徑；不建立目錄 |
| `acg memory enable` | 預檢、備份、建立索引與連結、安裝規則；有 remember 時處理其日誌設定 |
| `acg memory disable` | 移除 acg 管理的入口與日誌設定，保留記憶和既有日誌內容 |
| `acg memory adopt` | 將目前專案日誌移入可同步的 `projects/<key>/journal/`，保留本機連結 |
| `acg memory release` | 將目前專案日誌移回本機普通目錄；資料庫會產生日誌刪除差異 |
| `acg memory push`／`acg push memory` | 檢視 memory 範圍，確認後提交及推送；不 gather 記憶 |
| `acg pull` | 更新整個資料 repository；設定／skills 尚須 apply，記憶直接生效 |

上述 enable／disable／adopt／release 不自動提交或推送。日誌 release
不是刪除歷史：本機內容保留，Git 中的移除須另行檢視、提交與同步。
另一台機器的既有日誌連結會受到後續 pull 影響，確認畫面必須說明。
adopt 可以建立缺少的共用連結，不代表已安裝三個 AI 的記憶規則。

remember 日誌與整理過的記憶分開：日誌是唯讀的進度參考，由外掛
維護；只有 adopt 後的專案日誌跟隨 Git。enable 不自動 adopt 全部
專案、不安裝 remember、不接管外掛已有的其他 `data_dir`。

### 專案內的本機日誌入口（Draft）

啟用記憶時，acg 安裝本機 Claude SessionStart／UserPromptSubmit hook。
remember 完成搬移後，hook 將缺少或只剩有效搬移通知的 `<project>/.remember`
改為指向實際日誌的 symlink／Windows Junction。保留本機日誌的 Git
排除規則，不為了建立入口而 adopt 或上傳。UserPromptSubmit 補足
SessionStart hooks 並行時 remember 尚未完成初始化的情況。

hook 只在 acg 記憶仍啟用、data_dir 仍由 acg 管理且目的地存在時執行；
不搬日誌、不建立新的日誌資料、不碰 HOME 的 `.remember` 設定目錄。
原目錄有其他內容、通知指向不同目的地、未知連結或父路徑不安全時，
保留原狀並回報。入口建立失敗時還原原通知目錄。停用移除 acg 自己的
hook，保留專案入口及資料；入口使用實際資料路徑，不依賴共用別名。

hook 的執行檔路徑屬本機設定：init／status 排除，settings apply 保留
本機版本，其他 hooks 照常同步。規則與入口是否在新會話生效另行驗證。
使用 Claude 的 [exec-form hook](https://code.claude.com/docs/en/hooks#exec-form-and-shell-form)，
以 command／args 直接啟動執行檔，避免不同 shell 對空白與特殊字元的解讀。

## 規則入口與讀寫歸屬

Claude 使用 acg 的 `HOME/.claude/CLAUDE.md`；Codex 使用 acg 的
`HOME/.codex/AGENTS.md`，支援獨立檔案及既有合法的 Claude 共用連結。
此處沿用 `paths.py` 的 home 規則，不新增依 `CODEX_HOME` 或其他
工具環境變數自動切換目標的能力。狀態須顯示 acg 實際操作的路徑；
自訂工具 home 需已有對應的合法共用連結，否則不宣稱已設定該 home。
不可假設所有使用者已建立規則連結，也不掃描或改寫所有命名帳號 home。
`AGENTS.override.md` 遮蔽須分別回報，不能以「區塊存在」當成確定載入。

agy 目前候選入口為 `~/.gemini/config/rules/acg-memory.md`。
**檔案已安裝不等於 CLI 會載入**；需以實際支援版本的新會話驗證。
Antigravity IDE 的 GEMINI.md 文件不構成 agy CLI 的驗收證據。

管理區塊要求：先讀全域 `MEMORY.md`，再讀目前專案索引（存在時），
依相關索引按需讀 `topics/`；目錄不存在就略過。明確要求「記住」時，
專案決定與陷阱寫專案層，使用者偏好及跨專案事實寫全域層；歸屬
不明就詢問。每則記錄包含結論、日期、適用範圍與來源，不保存憑證、
完整聊天紀錄或未證實猜測。專案索引可在首次寫入時建立。

只有共用目錄是這類明確記憶要求的寫入目的地；內建自動記憶的搬移
另案處理。指示不依賴 Claude 專用匯入語法，也不要求 acg 必在 PATH。
新會話才作為入口變更的驗收單位，既有會話不保證重新載入。
多個 AI 同時寫檔不受 acg 操作鎖約束；不能承諾自動解決模型寫入競態。

## 共用 Git 與功能邊界

pull 更新整個 branch，沒有分類下載。沿用 fast-forward 契約：
已追蹤 dirty、進行中的 Git 操作、分歧或本機 ahead 狀態先拒絕，
不自動 stash、不挑選衝突版本。未追蹤新檔不一律阻擋；若將被遠端
更新覆蓋則讓 Git 拒絕，保留原檔。不能說所有「記住」都會阻擋 pull，
因為新建筆記可能尚未追蹤。只存在 memory 的 tracked 修改時提示
`acg memory push`；混合變更列出全部範圍，不指示強行略過其他變更。

`push memory` 只選記憶提交；`push all` 包含記憶與所有工具 gather。
同一個 Git push 仍推送完整 commit 歷史，跨範圍的 ahead 提交必須
依既有守衛拒絕，不能只展示 memory diff 就推送。GUI 不增加繞過
秘密掃描、範圍檢查或既有暫存拒絕規則的按鈕。

enable／disable 目前會更新已有的資料庫規則來源，可能讓 memory
以外的設定目錄也變髒；預覽必須逐一列出，不得把這些規則偷偷納入
memory 提交。必要時使用者經「上傳全部」完整檢視後一次保存。
一般 apply 與分享／取消分享／打包 skills 不操作 memory 資料。
新增類別套用時，規則檔內 acg memory 區塊也須維持本機入口狀態，
此項隔離需求尚待實作，詳見介面規格。

## 安全、備份與失敗行為

所有寫入先驗證資料根、父路徑、直接連結目標、symlink／Junction／
reparse point 與內容邊界；未知同名檔案或外部連結拒絕接管。
agy 管理區塊以外的內容保留。缺少整個記憶根目錄不能當成全部刪除
後推送；單一筆記的有意刪除仍可在正常檢視後提交。

操作鎖內再次預檢，使用唯一備份名稱與原始路徑 manifest，包含受
影響的本機／資料庫規則與 remember 設定。日誌同名版本都保留，
搬移或建立連結失敗應回復已修改部分。復原失敗須明列殘留與備份
位置，不能回報成功；Git 歷史不能取代未提交資料的備份。

路徑或檔案被外部程式改變時，拒絕過期預覽；預檢與實際寫入之間
仍有外部競態，操作鎖與原子寫入不構成跨 AI 的交易保證。
