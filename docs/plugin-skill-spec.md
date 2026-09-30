# plugin 改成兩層技能(`/acg <子指令>`)規格

狀態:**Done**(2026-09-30 使用者確認)。

## 目的

現在 plugin 帶 10 個斜線指令(`/acg:status`、`/acg:handoff`…)和一個
thread-handoff 技能。每個指令的說明在每次請求都會載入,而且模型只能靠另一個
技能才會在使用者說「交接」時主動做事。

改成一個 `acg` 技能,在 SKILL.md 放子指令對照表,每個子指令的做法放在
`references/<子指令>.md`,需要時才讀。做法參考 `/claude-api <subcommand>`。

## 已實測的前提

2026-09-29 以暫時的測試 plugin(`--plugin-dir`)實測,Claude Code 2.1.284:

| 情況 | 結果 |
| --- | --- |
| plugin 內技能在清單上的名稱 | `<plugin>:<技能>`,例如 `zqx:zqx` |
| 直接打 `/<技能> handoff` | 叫得到,`handoff` 以 `$ARGUMENTS` 傳入 |
| 打 `/<plugin>:<技能> handoff` | 也叫得到 |
| 同時存在同名的本機技能 | `/<技能>` 叫到本機那個,plugin 的只能用全名叫 |

所以 `/acg handoff` 可行,前提是 Claude 這邊沒有另一個叫 `acg` 的技能。

## 設計

### 技能本體

- `plugin/skills/acg/SKILL.md`:frontmatter 的 description 同時涵蓋「使用者打
  `/acg`」與「使用者說交接、接手、同步設定」等意圖,讓模型也能主動叫用。
- 本文是子指令表:子指令 → 參考檔 → 一句用途。沒給子指令時,依使用者的話判斷;
  判斷不了就列出子指令。
- `plugin/skills/acg/references/`:每個子指令一份,內容由現有的
  `plugin/commands/*.md` 搬過來,同時帶入 prompt audit 找到的三個修正
  (handoffs 的 `◐` 狀態、歸檔條件、memory 的「三件事」)。
- thread-handoff 技能併入,不再單獨存在。

### 子指令

`status`、`sync`、`save`、`share`、`memory`、`keepalive`、`msg`、`handoff`、
`handoffs`、`pickup`。`deploy` 是互動式選單,技能裡只說明
`acg deploy --profile` 與 `deploy --remove` 怎麼用,不代替使用者勾選。

### 跟現在不同的地方

- **不能預先執行**:現有指令用 `!` 在載入時先跑 `acg status` 等。技能無法依子指令
  決定要不要預跑,改成由模型用 Bash 執行。
- **權限合併成一份**:`allowed-tools` 改寫在技能的 frontmatter,是各子指令的聯集。
- **只能由使用者叫用的設定消失**:status、memory、keepalive 原本
  `disable-model-invocation`。它們都是唯讀,模型主動叫用沒有風險。

### 全域 `acg` 使用說明技能讓位

資料庫的 `claude/skills/acg`(使用說明)會搶走 `/acg`。把它從 `claude/skills/`
移到 `claude/shared/both/`,只給 Codex 與 agy。Claude 這邊改由 plugin 技能提供,
使用說明的全文仍可用 `acg skill` 印出,技能本文只放指向它的一句話。

## 使用者的決定(2026-09-29)

1. 舊的 `/acg:xxx` 指令在同一版直接移除,不留別名。
2. 使用說明技能移到共用區,Claude 這邊只剩 plugin 的 `acg` 技能。

## 權限(已實測)

- 使用者打 `/acg …` 叫用時,技能 frontmatter 的 `allowed-tools` 生效;模型依描述
  自行觸發時不生效,改由當下的權限模式決定。舊的 thread-handoff 技能同樣如此。
- 讀取技能自己的參考檔也需要權限。`allowed-tools` 加上
  `Read(~/.claude/plugins/cache/acg/**)`,只放行已安裝 plugin 的目錄。實測限定路徑的
  寫法有效,指到別處則讀不到。`${CLAUDE_SKILL_DIR}` 寫法沒有效果。

## 要一起改的地方

- `plugin/`:技能、參考檔,`plugin.json` 版號隨發版。
- `ai_config/guide.py` 的 Slash commands 一節、`__main__.py` 的 `--help` 末段、
  README 的 plugin 一節。
- 程式裡提到 `/acg:handoff`、`/acg:pickup` 的提示字串:
  `handoff_reminder.py`(交接提醒與 status line)、`commands/memory_handoff.py`。
- 測試:`tests/test_release_contract.py` 裡檢查 `plugin/commands` 的三個測試,
  以及 `tests/test_handoff.py` 對 `/acg:pickup` 字樣的斷言。
- 桌面版沒有引用這些指令,不用改。

## 驗證

- 單元測試:技能 frontmatter、子指令表裡每個參考檔都存在、參考檔裡沒有會被
  直接執行的佔位符。
- 實機:用 `claude plugin install` 裝到這台(不是 `--plugin-dir`),開新 session
  逐一打 `/acg <子指令>`,並用一句「交接」確認模型會主動叫用。
- 三台 update 後各跑一次 `/acg status`。

## 修訂:選單項目與技能並用(2026-09-29)

選單上只看得到 `acg:acg`,選了之後也不會列出子指令。官方文件確認一般技能沒有
可點選的子指令清單,只有 `argument-hint` 這段灰字提示。

依文件,`disable-model-invocation: true` 的項目「說明不進 context,使用者叫用時才載入」。
所以改成:

- 每個子指令一個 `plugin/skills/<子指令>/SKILL.md`,設為只給使用者叫用,選單上是
  `/acg:<子指令>`,不佔 context。做法就寫在這份檔裡。
- `acg` 技能保留,負責自然語言觸發與 `/acg <子指令>`,讀的就是上面那些檔,做法只有一份。
- 舊的十個指令裡只有三個是只給使用者叫用,其他七個的說明原本都在 context 裡。
