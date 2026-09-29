# 專案層部署(deploy)規格

狀態:**Draft**。使用者確認前不改為 Done。

## 目的與範圍

平常 `apply` 把技能、plugin、規則裝在各工具的全域位置,整台機器都生效。
在別人的主機上開發時,不該動到主機擁有者的全域設定。`deploy` 讓人從資料庫
挑選要的東西,改裝到**自己的專案目錄**,只在那個目錄生效。

跟版本控制無關:放進去的檔案要不要提交,由使用者自己決定。

## 放置位置

每一項照全域時給哪些工具,放到那些工具在專案層會讀的位置。
下表的讀取位置是 2026-09-29 以無頭模式開新 session 實測的結果
(Claude Code 2.1.284、Codex 0.156.1、agy 1.2.12)。

| 資料庫來源 | 全域時給誰 | 專案內放置 | 實測誰會讀 |
| --- | --- | --- | --- |
| `claude/skills/<名稱>` | Claude | `.claude/skills/<名稱>` | 只有 Claude |
| `claude/shared/both/<名稱>` | Codex、agy | `.agents/skills/<名稱>` | Codex、agy |
| `claude/shared/codex/<名稱>` | Codex | `.codex/skills/<名稱>` | 只有 Codex |
| `claude/shared/agy/<名稱>` | agy | `.agent/skills/<名稱>` | 只有 agy |
| `claude/CLAUDE.md` | Claude | `.claude/CLAUDE.md` | Claude |
| `claude/rules`、`agents`、`commands` | Claude | `.claude/` 同名目錄 | Claude |
| `claude/settings.json` 的 `enabledPlugins` | Claude | `claude plugin install --scope project` | 只有該專案的 Claude |

同名技能在選單裡是一項,選了就放到它全域時會去的所有位置。

Plugin 只有 Claude 支援專案範圍。實測專案範圍安裝只在專案的
`.claude/settings.json` 加上 `enabledPlugins`,主機的 settings.json 不變,
專案外的新 session 看不到它。Claude 自己的 plugin 登記檔與快取仍在家目錄,
那是 Claude 的記錄,不會讓主機別處啟用。主機不認得那個 marketplace 時,
以 `--scope project` 宣告在專案裡再安裝。Codex 與 agy 沒有專案範圍的
plugin,不處理。

不再部署:個人的整份 `settings.json`(主題、狀態列等個人偏好,且會蓋掉專案
原有設定)、`mcp.json`(專案 MCP 的位置是專案根目錄的 `.mcp.json`,放在
`.claude/` 不會被讀)、`statusline.sh`。

## 不破壞專案既有內容

- 絕不刪除專案裡的任何檔案。
- 目的地已存在且內容相同:視為已就緒。
- 目的地已存在但內容不同:略過並列出,不覆蓋。要換新版就先自己刪掉。
- 規則、agents、commands 以單一檔案為單位合併,團隊原有檔案保留。
- 技能以整個目錄為單位,避免新舊版本的檔案混在一起。
- 有任何一項沒放成(衝突或 plugin 安裝失敗),結束碼為 1。

## 不碰家目錄

`deploy` 只寫專案目錄。唯一的例外是 Claude plugin 安裝時 Claude 自己
更新的 plugin 登記與快取。2026-09-29 以假家目錄模擬新主機驗證:`setup`
只新增 acg 自己的設定檔與資料庫副本,`deploy` 對家目錄零寫入。

## 設定組

`--save-as <名稱>` 把這次的選擇存進資料庫,`--profile <名稱>` 重播。
選項名稱是 `CLAUDE.md`、`rules`、`skills/<名稱>`、`plugins/<plugin id>` 等,
跟選單上顯示的一致。

## 專案層記憶

選 `memory` 時,把共用記憶規則寫成專案根目錄 `AGENTS.md` 裡的一段區塊
(標記 `acg:project-memory`),檔案不存在就新建。2026-09-29 以禁用讀檔工具的
新 session 實測:Claude Code 2.1.284、Codex 0.156.1、agy 1.2.12 都讀專案根目錄
的 `AGENTS.md`;agy 在無頭模式不讀 `.agent/rules/`,Claude 以外不讀
`.claude/rules/`。所以一處就夠三個工具讀到。

規則內容跟全域那份相同,只是把 `~/.claude/shared-memory` 換成這台資料庫
記憶目錄的實際路徑。不建立全域連結、不裝全域規則或 hook。記憶本身仍在
acg 的資料庫副本裡,跟著 pull/push 同步。

區塊已存在且內容相同視為已就緒;內容不同(例如換了資料庫位置)就更新,
因為那段是 acg 自己的。

## 撤除

每次部署把放了什麼記在專案根目錄的 `.acg-deploy.json`:每個檔案的相對路徑
與放入時的 sha256、裝的 plugin、宣告的 marketplace、有沒有加記憶區塊以及
`AGENTS.md` 是不是 acg 建的。放到一半失敗也會記下已放的部分。

`deploy --remove [dir]` 先列出要做的事,確認後:

- 內容跟放入時相同的檔案刪掉,刪完留下的空目錄一併移除。
- 放入後被改過的檔案保留並列出,結束碼為 1,紀錄保留這些項目。
- Plugin 與 marketplace 以 `--scope project` 移除。
- Claude 移除 plugin 後會重新排版專案的 `.claude/settings.json`,並留下空的
  `enabledPlugins`。部署時先記下原文;撤除時若除了那個空欄位之外語意相同,
  就寫回原文,原本沒有這個檔就刪掉。部署後有人改過設定就保持現狀。
- 從 `AGENTS.md` 拿掉 acg 區塊;檔案是 acg 建的且沒剩其他內容就刪掉。
- 全部收回後刪除紀錄檔。

紀錄檔在專案裡,可能被別人改過,所以裡面的路徑一律當成不可信:
必須是專案內的相對路徑、不含 `..`、沿途沒有符號連結或 junction,
否則不處理並列出。

## 桌面版

首頁的「專案」入口開啟同一套功能,與專案記憶同一頁、同一次選擇:
選資料夾拿到後端發的 token(記憶與部署共用),勾選
項目後預覽,確認時後端先重算預覽,跟看到的不同就拒絕。收回也是先預覽再
確認。桌面版與 CLI 讀寫同一份 `.acg-deploy.json`,彼此可以接著用。
