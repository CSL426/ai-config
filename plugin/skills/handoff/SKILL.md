---
name: handoff
description: 把這條工作線的進度交接給下一個 session,或管理 context 提醒
argument-hint: '[工作線名稱] [進度與下一步] | remind [status|enable [百分比]|disable]'
disable-model-invocation: true
allowed-tools: Bash(acg memory handoff:*), Bash(ai-config memory handoff:*)
---

# handoff:把這條工作線交接給下一個 session

使用者的流程是:交接 → `/clear` → 接手,同一個 session 接續同一條線。交接取代壓縮:
只帶整理過的進度,不帶整段對話裡對的錯的混在一起的歷史。

## 管理 context 提醒

若引數以 `remind` 開頭,或使用者要求查看、啟用、調整、停用交接提醒,執行對應指令並回報
結果,到這裡結束,不建立交接:

```
acg memory handoff remind status
acg memory handoff remind enable [1 到 99 的百分比]
acg memory handoff remind disable
```

`remind` 沒指定動作時查 `status`。`enable` 預設門檻 70%,要用使用者指定的數值。

這是 Claude Code 專用的本機功能。`acg memory enable` 會一併以 70% 開啟(已設定的門檻保留),
`acg memory disable` 會移除;只想關提醒就用 `remind disable`。它讀取官方的 context 使用
百分比,保留既有的 statusLine 輸出,在送出提示或工具完成時,到門檻只提醒一次,壓縮後重設。
提醒不會自動寫交接;PreCompact 只重設狀態,不要求寫交接,也不阻擋壓縮。

## 做完了就不寫交接

先判斷這條線還有沒有事要做。Next 寫不出任何一項,表示這條線已經完成:接手時那則交接就已經
結案了,什麼都不用寫,告訴使用者這條線做完了。不要寫一份「沒有下一步」的交接,那只會讓它
出現在待接清單上。

## 寫入交接

引數的第一個詞是工作線名稱,其餘是內容。沒給或給得不完整,你要自己補:

- **名稱**:用這個 session 實際在做的事命名,簡短好認,例如「記憶改善」「GUI 改版」。
  不要用 session id。
- **內容**:寫給不知道前因後果的人看。用下面這幾個標題分段,接手的人才不必讀完整篇才知道
  還剩什麼(`list` 會自動顯示 `## Next` 的第一項):

      ## Goal       這條線要達成什麼
      ## State      現在到哪裡:分支、版本、部署狀態
      ## Verified   實際驗證過的結論,附上怎麼驗的
      ## Refuted    試過但不成立的,寫下來才不會有人再試一次
      ## Unknowns   還不確定的,別寫成結論
      ## Next       還沒做的,一項一行

  Verified 跟 Unknowns 要分開:把推測寫得像事實,下一個人會照著做決定。沒東西可寫的段落就
  省略,不要留空標題。「繼續」這種沒有資訊的句子不算內容。

決定好名稱與內容之後執行(名稱含空格要用引號):

```
acg memory handoff write "<名稱>" "<內容>"
```

這行是格式示範,要換成你決定的名稱與內容。執行完把結果回報給使用者,告訴他可以 `/clear`,
之後打 `/acg pickup` 或說「接著做」就能接回來。

引數:`$ARGUMENTS`
