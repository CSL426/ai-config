# 專案層技能同步(skill sync)規格

狀態:**Draft**。

## 目的與範圍

專案自己的技能不該寫三份。Codex 與 Antigravity 都讀 `.agents/skills/`,只有 Claude
讀 `.claude/skills/`,所以技能只寫在 `.agents/skills/`,由 `acg skill sync` 複製給
Claude。

這跟 `deploy` 不同:`deploy` 把資料庫的東西放進專案,`skill sync` 的來源就是專案本身,
不讀資料庫,所以 CLI 在 `setup` 之前也能跑(桌面 app 的專案頁仍要先設定好)。

## 各工具實際讀哪裡

2026-11-04 在 Linux 以無頭模式開新 session 實測(Claude Code、Codex 0.166.0、
agy 1.4.12),請工具列出已載入的技能:

| 工具 | 專案內讀到的目錄 |
| --- | --- |
| Claude Code | `.claude/skills` |
| Codex | `.agents/skills`、`.codex/skills` |
| Antigravity | `.agents/skills`、`.agent/skills` |

## 指令

- `acg skill sync [目錄]`:同步,目錄預設是目前目錄。有衝突時其他項目照做,結束碼為 1。
- `acg skill sync [目錄] --check`:不寫入,只回報。有任何一項不是「已一致」,結束碼為 1,
  可以接進 CI。

## 要提交什麼

`.claude/skills/` 的副本與 `.acg-skill-sync.json` 都要跟著專案提交。副本被 gitignore
的話,另一個 clone 拉到新來源與新紀錄時,舊副本對不上紀錄,會被當成有人改過。

## 紀錄檔

`.acg-skill-sync.json` 放專案根目錄,格式是 `{"skills": {"<名稱>": "<指紋>"}}`。指紋是
SHA-256,涵蓋副本裡所有檔案的相對路徑與內容,不含權限。它讓 acg 分得出「自己放的
副本」和「別人手寫的同名技能」。紀錄檔內容不合法時直接拒絕,因為名稱會被拿去組
路徑與刪除。

兩個分支改到紀錄檔而合併衝突時:解掉衝突標記(任一邊的內容都行),再跑一次
`skill sync`。來源與副本一致的項目會自動補回正確指紋。

指紋把 CRLF 視為 LF:同一份提交在 `core.autocrlf` 的 Windows 上取出來,不能被當成
有人改過。來源與副本是否一致則比對原始位元組與可執行位元(Windows 沒有可執行位元,
不比)。

## 每個技能怎麼處理

來源是 `.agents/skills/` 底下含 `SKILL.md`、名稱不以 `.` 開頭的目錄。

| 情況 | 動作 |
| --- | --- |
| 副本不存在 | 建立 |
| 副本與來源相同 | 已一致;紀錄缺少或過期就更新 |
| 副本與來源不同,副本是紀錄裡的那份 | 更新 |
| 副本與來源不同,副本被改過或不是 acg 放的 | **衝突**,不動 |
| 來源已刪,副本是紀錄裡的那份 | 刪除副本 |
| 來源已刪,副本被改過 | **衝突**,不動 |
| 來源已刪,副本也不在 | 清掉紀錄 |
| 來源還在、但不是可同步的技能,且有紀錄(沒有 `SKILL.md`、被換成檔案、是 deploy 放的) | **衝突**,不動,避免誤刪副本 |
| 同上,但沒有紀錄 | 略過 |
| `.agents/skills` 整個不在,但紀錄有項目 | **拒絕整次同步**:sparse checkout 或未初始化的 submodule 不能被當成全部刪掉 |

要移除最後一個技能:連同副本與紀錄檔一起刪掉。來源目錄空了 Git 不會保留,所以只刪
來源會落到上面「拒絕整次同步」那一列。

## 跟 deploy 的關係

`.acg-deploy.json` 列管的技能是資料庫裡的個人技能,不是這個專案的,不當來源,否則會
被複製進要提交的副本與紀錄。

## 安全

- 專案是家目錄、家目錄的上層或資料庫時拒絕:`~/.agents/skills` 是 Codex 的全域目錄,
  把它當來源會改寫甚至刪除全域的 Claude 技能。
- `.agents`、`.agents/skills`、`.claude`、`.claude/skills`、紀錄檔、來源與副本裡只要
  有 symlink、junction 或 reparse point 就拒絕,一律不跟隨。任一技能裡有 symlink 會
  中止整次同步,先前已完成的項目會留下並記進紀錄。
- 路徑上任何一段是憑證檔名(`EXCLUDED_FILES`)或 `.git` 都不複製。`.env` 這類不在清單裡
  的檔案會照常複製,所以機密不要放進技能目錄。副本裡多出來的檔案算改過,不會被更新或
  刪除帶走。
- 名稱不能含路徑或 Windows 不接受的字元,不能以 `.` 開頭或結尾,也不能是 `CON`、`NUL`
  這類保留名稱。
- 一律複製、不用 symlink:Windows 建 symlink 需要多數帳號沒有的權限。
- 先寫到 `.claude/skills/` 底下的暫存目錄,換上去或刪除的前一刻再確認一次副本沒變;
  規劃之後才出現的手寫副本也算。
- 不碰家目錄與資料庫。

## 各介面

CLI、`--help`、agent 指南(`guide.py`)、桌面 app 的專案頁(預覽後確認)、plugin 的
`/acg:skills`。`skill` 的子指令本來就沒有補全,`sync` 也沒有。

## 尚未驗證

Windows 上大小寫改名時的行為、副本檔案被占用時 `os.replace` 失敗的處理(其餘項目會中止,
已完成的會記進紀錄),都還沒在真的 Windows 跑過。
