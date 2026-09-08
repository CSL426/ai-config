# 主機設定組

狀態：Draft。此文件是設計提案，相關指令尚未實作。

## 需求

一份 `main` 是所有機器的共同基底。某台機器（例如 A4000、gb10）
有自己的差異時，把差異存成一個命名的設定組。設定組可以有很多個。
新機器第一次設定時，從清單裡挑一個設定組套用，或只用 main。

既有的 `deploy-profiles.toml` 是部署 skill 時的選擇清單，與本提案
無關；為避免混淆，本提案不用「profile」當指令名，一律稱主機設定組，
指令群組為 `acg host`。

## 覆蓋而非複製

設定組只保存與 main 不同的部分，不是整份複製。整份複製會讓共同
的修改要重做 N 次，正是 acg 要避免的事。acg 在投影時把 main 與
設定組疊起來，各工具看到的是疊完的結果。

```text
<data-repo>/
├── claude/                     main：所有機器的共同基底
├── codex/
├── agy/
├── memory/                     不受設定組影響
└── hosts/
    ├── A4000/
    │   ├── host.toml           說明與移除清單
    │   ├── claude/
    │   │   ├── mcp.json        只放與 main 不同的伺服器
    │   │   └── settings.json   只放與 main 不同的鍵
    │   └── codex/
    │       └── config.toml     只放與 main 不同的表
    └── gb10/
        └── ...
```

沒有 `hosts/main/`：main 就是工具目錄本身。一台機器同時只能綁定
一個設定組，或不綁定（等於 main）。多台機器可以綁同一個設定組。

## 疊合規則

按檔案類型決定怎麼疊，規則要少到能在 status 裡一句話講清楚：

- `settings.json`、`mcp.json`（Claude、agy 的 JSON 設定）：頂層鍵
  合併，設定組的鍵覆蓋 main 的同名鍵，不做更深層合併。MCP 伺服器
  這類物件在頂層鍵之下按名稱覆蓋，不合併單一伺服器的欄位。
- `config.toml`（Codex）：頂層表合併，設定組的表覆蓋 main 的同名表。
- `CLAUDE.md`、`AGENTS.md`、`statusline.sh` 與其他純文字檔：整檔
  取代，不做文字合併。
- `skills/`、`rules/`、`agents/`、`commands/` 與 `shared/`：按項目
  名稱疊加，設定組有同名項目就取代，沒有就新增。要拿掉 main 的
  項目用 `host.toml` 的移除清單，不用空目錄表示刪除。

`host.toml` 只有兩件事：一段給人看的說明，以及按工具列出的移除
清單。設定組裡的機器本機欄位（permissions、trustedWorkspaces、
notify、`[projects.*]`）維持既有排除規則，不因為放進設定組就變成
可同步。

## 綁定與指令

機器綁定哪個設定組是本機狀態，存在 acg 自己的設定檔
（`~/.config/ai-config/config.json` 或各平台對應位置）的
`host_profile` 欄位，不進資料 repo。

| 指令 | 行為 |
| --- | --- |
| `acg host list` | 列出所有設定組、說明、各覆蓋了哪些檔案；標出這台目前綁定的 |
| `acg host use <名稱>` | 綁定這台機器；之後的 status／apply／push 都以 main 疊上它為準 |
| `acg host use main` | 解除綁定 |
| `acg host save <名稱>` | 把這台目前的即時設定與 main 比對，差異寫進設定組；已存在就更新 |
| `acg host diff [名稱]` | 唯讀顯示設定組相對 main 的覆蓋內容 |
| `acg host drop <名稱>` | 刪除設定組；有機器綁定它時列出警告並要求確認 |

`acg setup` 在資料 repo 就緒後，若 `hosts/` 不是空的，列出清單讓
使用者挑一個或選 main。GUI 設定對話框提供同一個選擇，主畫面在
資料位置旁標出目前綁定的設定組名稱。

## 修改要進哪一層

這是本提案最容易做錯的地方。綁定設定組的機器改了設定之後執行
`acg push`，每個變更只能進一層：

- 變更的檔案或鍵已經在設定組裡：進設定組。
- 不在設定組裡：進 main，所有機器都會拿到。
- push 的預覽逐項標出去向；使用者可以用 `--host` 把整批改進
  設定組，或用 `--main` 整批進 main。兩個旗標互斥，都不給就用上面
  的預設規則。

同一批變更同時進兩層時，提交拆成兩個 commit，各自只碰自己的層。
既有「不混入預先暫存變更」與憑證掃描規則不變。

status 對綁定設定組的機器要顯示三件事：目前設定組名稱、各工具
與疊合結果的差異、每個差異預設會進哪一層。

## 新機器流程

1. `acg setup` 取得資料 repo。
2. 從 `hosts/` 清單挑一個設定組，或選 main。
3. `acg apply` 以疊合結果投影到各工具。
4. 之後在這台調整的設定，push 時按上一節規則進對應的層。

從一台既有機器建立新設定組：`acg host save A4000` 會把這台與
main 的差異存進去，再 `acg host use A4000` 綁定，之後 push。

## 安全

- 設定組名稱沿用 `deploy-profiles.toml` 的名稱規則，拒絕路徑分隔
  符號與開頭的點。
- `hosts/<名稱>/` 下的路徑必須落在對應工具的既有管理範圍內，
  不能透過設定組把檔案投影到管理範圍以外。
- 設定組內容與 main 一樣經過憑證掃描與機器本機欄位過濾。
- `host drop` 先備份被刪的設定組，並沿用既有備份保留數。
- 疊合只在暫存投影階段發生，不改資料 repo 裡 main 或設定組本身。

## 程式整合位置

- 新增 hosts.py：設定組載入、疊合、差異計算與 `host.toml` 讀寫。
- config.py：`host_profile` 欄位與驗證。
- staging.py 與 tools/claude.py、tools/codex.py、tools/agy.py：投影
  來源從單一工具目錄改為疊合結果。
- commands/push.py：變更分層、預覽標示、`--host`／`--main`、分層提交。
- commands/status.py：設定組名稱與分層標示。
- commands/setup.py：設定組選擇。
- 新增 commands/host.py；__main__.py、completion.py、guide.py：
  指令、補全與說明。
- GUI：設定對話框的選擇與主畫面標示。

## 驗收

隔離 HOME 與資料 repo，測試：未綁定時行為與現況完全相同；綁定
後 JSON 頂層鍵覆蓋、TOML 頂層表覆蓋、純文字整檔取代、目錄項目
新增與取代、移除清單生效；機器本機欄位在設定組內同樣被過濾；
`host save` 只寫差異且第二次執行不產生變更；push 的分層預設規則、
兩個旗標、分層提交各自只碰自己的層；`host drop` 有備份且對綁定
中的機器提出警告；設定組名稱與路徑越界被拒絕；setup 與 GUI 的
選擇寫進本機設定檔而不進資料 repo。
