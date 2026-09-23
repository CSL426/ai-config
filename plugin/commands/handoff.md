---
description: 把這條工作線的進度交接給下一個 session，或管理 context 提醒
argument-hint: '[工作線名稱] [進度與下一步] | remind [status|enable [百分比]|disable]'
allowed-tools: Bash(acg memory handoff:*), Bash(ai-config memory handoff:*)
---

使用者要把目前這條工作線交接出去,給接手的下一個 session 讀。

引數:`$ARGUMENTS`

## 管理提醒

若引數以 `remind` 開頭，或使用者明確要求查看、啟用、調整、停用交接提醒，
執行對應指令並回報結果，到這裡結束，不建立交接：

```bash
acg memory handoff remind status
acg memory handoff remind enable
acg memory handoff remind enable 80
acg memory handoff remind disable
```

`remind` 未指定動作時查詢 `status`。`enable` 預設門檻 70%，可接受 1 到 99
的整數。上面的 80 是指定門檻的範例，應使用使用者要求的數值。

這是 Claude Code 專用、本機選擇啟用的功能。讀取官方 context 使用百分比，
保留既有 statusLine 輸出，在送出提示或工具完成時，到門檻只提醒一次；
壓縮後重設。無讀值、null 或讀值過期時略過，不要求事先 claim 工作線。
提醒不會自動寫入交接；PreCompact 只重設狀態，不要求模型寫交接，也不阻擋壓縮。

## 做完了就結案,不寫交接

先判斷這條線還有沒有事要做。Next 寫不出任何一項,表示這條線已經完成:改用
`done` 結案,告訴使用者已結案,到這裡結束。不要寫一份「沒有下一步」的交接,
那只會讓它一直留在待接清單上。

```
acg memory handoff done "這條線的名稱"
```

還有事沒做完,才往下寫交接。

## 寫入交接

第一個詞是工作線名稱,其餘是內容。若使用者沒給,或給得不完整,你要自己補:

- **名稱**:用這個 session 實際在做的事命名,簡短好認,例如「記憶改善」「GUI 改版」。不要用 session id。
- **內容**:寫給不知道前因後果的人看。用下面這幾個標題分段,接手的人才不必
  讀完整篇才知道還剩什麼(`list` 會自動顯示 `## Next` 的第一項):

      ## Goal       這條線要達成什麼
      ## State      現在到哪裡:分支、版本、部署狀態
      ## Verified   **實際驗證過**的結論,附上怎麼驗的
      ## Refuted    試過但不成立的,寫下來才不會有人再試一次
      ## Unknowns   還不確定的,別寫成結論
      ## Next       還沒做的,一項一行

  **Verified 跟 Unknowns 一定要分開。** 把推測寫得像事實,下一個人會照著做
  決定。沒東西可寫的段落就省略,不要留空標題。不要只寫「繼續」這種沒有資訊
  的句子。

決定好名稱與內容之後,用 Bash 執行(名稱含空格要用引號):

```
acg memory handoff write "你決定的名稱" "你寫的內容"
```

**不要照抄上面的字面值**,那是格式示範。執行完把結果回報給使用者,並提醒下個
session 可以用 `/acg:pickup` 接手。
