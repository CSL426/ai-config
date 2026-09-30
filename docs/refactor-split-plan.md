# 程式碼拆分計畫

狀態:**Done**(2026-09-30 使用者確認)。

## 目標

把目前同時承擔多個責任的巨大模組拆成可測試、可定位的模組，維持
CLI、GUI、Windows 原生行為與安全邊界不變。每一階段都要先搬移測試與
所有引用，再刪除原本的巨大檔案；不留下只轉呼叫的舊 monolith。

## 執行進度

- Phase 1 GUI：已完成拆分與引用遷移；舊 `ai_config/commands/gui.py` 已刪除。
- Phase 1 驗證：1141 tests 通過、12 個 skip；`ruff`、`bash -n install.sh`、
  `git diff --check` 通過。
- Phase 2 Push：已完成。`commands/push.py` 由 1252 行縮成 97 行的 `do_push()`；
  檢查、審閱、發布分到 `push_preflight.py`、`push_review.py`、`push_publish.py`。
  模組之間一律以 `模組.函式()` 互相呼叫，測試用 `tests/push_helpers.py` 的
  `patch_push()` 一次替換所有模組上的同名綁定。push 測試由
  `test_packaging_and_sync.py` 搬到 `test_push.py`，共用的臨時資料庫輔助函式
  搬到 `tests/data_repo_helpers.py`。
- Phase 2 驗證：1183 tests 通過、12 個 skip，與拆分前相同；`ruff`、
  `bash -n install.sh`、`git diff --check` 通過；`rg` 確認沒有對
  `commands.push` 私有函式的舊引用。
- Phase 3 Memory：已完成。`ai_config/memory.py` 已刪除，分成 `memory_paths.py`
  （路徑、規則區塊、預檢與檔案寫入）、`memory_journal.py`（remember 日誌、
  adopt／release、專案入口）、`memory_index.py`（索引、drift、secret、status
  掃描）。會被測試替換的常數（`CLAUDE_HOME`、`SCRIPT_DIR`、`HOME` 等）只由
  `memory_paths` 擁有，其他模組以 `memory_paths.X` 在呼叫時讀取，所以替換一處
  就生效。`commands/memory.py` 縮成 158 行的 dispatcher，實作分到
  `commands/memory_lifecycle.py` 與 `commands/memory_handoff.py`；說明旗標集中為
  `console.HELP_FLAGS`。
- Phase 3 驗證：1183 tests 通過、12 個 skip；`rg` 確認沒有 `ai_config.memory`
  的舊引用。
- Phase 4 Setup 與 Google Drive：已完成。`gdrive.py` 已刪除，分成
  `gdrive_auth.py`（OAuth PKCE、token）、`gdrive_client.py`（API 請求、資料夾）、
  `gdrive_sync.py`（pull／push bundle）。`commands/setup.py` 縮成 CLI 入口，
  實作分到 `commands/setup_git.py` 與 `commands/setup_gdrive.py`。release
  workflow 注入 client ID 的目標改成 `gdrive_auth.py`，並新增
  `test_release_injection.py`：workflow 用 sed 寫入的每個常數都必須在目標檔裡。
  順手移除 workflow 裡早已失效的 GitHub client ID 注入（ffcea05 起改為寫死）。
- Phase 4 驗證：1186 tests 通過（多 3 個注入檢查）、12 個 skip。
- Phase 5 CLI dispatch 與次要模組：已完成。
  - `__main__.py` 縮成 130 行：保留 `usage()`，`main()` 把它交給
    `cli_dispatch.main()`；分派不 import `__main__`，`python -m ai_config` 才不會
    載入第二份。
  - `keepalive.py` 已刪除，分成 `keepalive_settings.py`、`keepalive_runner.py`、
    `keepalive_scheduler.py`、`keepalive_window.py`。
  - `ghauth.py` 已刪除，分成 `ghauth_login.py`、`ghauth_binding.py`、
    `ghauth_helper.py`、`ghauth_access.py`；原 docstring 的三段理由跟著各自的程式走。
  - 拆 ghauth 時發現 credential helper 綁的是解析後的 `versions/<版號>/` 路徑，
    版本目錄被清掉後第一次 push 會失敗；已改成綁固定入口，apply／update 會重新
    指向（3f66b37）。conftest 的整體防護也擴及資料庫的 `.git/config`。
- Phase 5 驗證：1188 tests 通過、12 個 skip；真實的 `~/.claude/settings.json` 與
  資料庫 `.git/config` 在整套測試前後不變。
- 測試拆分：已完成，四個大檔都已刪除。
  - `test_packaging_and_sync.py` → `test_push.py`（Phase 2）、`test_setup_pull.py`、
    `test_release_contract.py`。
  - `test_windows_sync.py` → `test_windows_cli.py`、`test_windows_apply_skills.py`、
    `test_windows_reparse_backup.py`。
  - `test_gdrive.py` → `test_gdrive_auth.py`、`test_gdrive_storage.py`、
    `test_gdrive_sync.py`；autouse 的 `_isolated_config` 放在
    `gdrive_test_helpers.py`，三個檔都 import。
  - `test_ghauth.py` → `test_ghauth_access.py`、`test_ghauth_binding.py`、
    `test_ghauth_helper.py`。
  - 多檔共用的輔助函式放在 `*_test_helpers.py`；只有一個檔用到的就跟著那個檔。
- 測試拆分驗證：1188 tests 通過、12 個 skip，與拆分前相同。

## 不變的契約

- 不改 CLI 指令、參數、輸出與 exit code。
- 不改 GUI bridge 的方法名稱、回傳資料形狀與預覽確認流程。
- 不削弱 symlink、junction、reparse point、credential 與 backup safety。
- 保留 Python 3.11、Linux 與 native Windows 行為。
- 不在此重構中加入新功能；每次搬移都要有回歸測試。

## 拆分順序

### Phase 1：GUI（已完成驗證）

來源：`ai_config/commands/gui.py`。

拆成：

- `ai_config/gui_api.py`：`GuiApi`、`_PromptInput`、GUI bridge 狀態與
  前端呼叫的方法。
- `ai_config/desktop.py`：資源定位、shortcut、console 顯示／隱藏、
  detach 與 pywebview 啟動。

同步更新 `__main__.py`、`cli.py`、GUI 測試、standalone 測試與 bridge
註解。確認所有引用改完、測試通過後，刪除：

- `ai_config/commands/gui.py`

### Phase 2：Push pipeline

來源：`ai_config/commands/push.py`。

拆成：

- `ai_config/push_preflight.py`：scope、工作樹、upstream、ahead/behind、
  credential 與 secret 掃描。
- `ai_config/push_review.py`：diff 顯示、review、確認、commit message。
- `ai_config/push_publish.py`：stage、commit、push 與已存在 commit 的
  發布流程。
- `ai_config/commands/push.py`：只保留薄 CLI adapter `do_push()`。

先更新 `autopush.py`、memory command、GUI 與所有測試的引用，再刪除舊
的巨大實作；若 `commands/push.py` 仍只是薄入口，保留它不算保留舊
monolith。

### Phase 3：Memory domain

來源：`ai_config/memory.py` 與 `ai_config/commands/memory.py`。

拆成：

- `ai_config/memory_paths.py`：路徑、規則區塊、純路徑與安全檢查。
- `ai_config/memory_journal.py`：journal link、adopt/release、搬移復原。
- `ai_config/memory_index.py`：index、drift、secret notes、status scan。
- `ai_config/commands/memory_lifecycle.py`：enable/disable、transaction、
  backup 與 host 操作。
- `ai_config/commands/memory_handoff.py`：handoff 與 reminder CLI adapter。
- `ai_config/commands/memory.py`：只保留子命令 dispatcher。

所有引用遷移完成且 `from ai_config import memory` 不再依賴舊檔後，刪除：

- `ai_config/memory.py`
- `ai_config/commands/memory.py` 中已搬走的巨大實作；若 dispatcher
  仍需要此路徑，改成新的薄入口檔，而不是保留舊內容。

### Phase 4：Setup 與 Google Drive

拆分：

- `commands/setup.py` → `setup_git.py`、`setup_gdrive.py`、薄 `setup.py`。
- `gdrive.py` → `gdrive_auth.py`、`gdrive_client.py`、`gdrive_sync.py`。

更新 CLI、GUI、sync、push、info 與測試引用。確認不再依賴舊實作後，
刪除原本的巨大 `setup.py` 實作與 `gdrive.py` 實作；留下的同名檔只能是
薄入口，不能再恢復所有責任。

### Phase 5：CLI dispatch 與次要模組

- `__main__.py` 保留必要的 `usage()` 契約，但把 `main()` 的命令解析與
  dispatch 移到 `cli_dispatch.py`，使 `__main__.py` 成為薄入口。
- `keepalive.py` 拆出設定、執行器、OS scheduler backend、window status。
- `ghauth.py` 拆出 device login、repo binding、credential helper、access
  diagnosis；此階段最後處理，因為安全邊界與 Windows Git 行為較敏感。

## 測試拆分

生產程式搬移時同步拆測試，不改測試涵義：

- `test_packaging_and_sync.py` → setup/pull、push、plugin/release contract。
- `test_windows_sync.py` → CLI/projection、apply/skills、reparse/backup。
- `test_gdrive.py` → auth、Git sync、folder/storage。
- `test_ghauth.py` → access/login、binding、credential helper。

## 每階段驗收

1. `rg` 確認沒有引用已搬走的舊模組或舊私有函式。
2. `python -m pytest`。
3. `ruff check ai_config tests`。
4. `bash -n install.sh`。
5. `git diff --check`，並獨立檢查 untracked files。
6. 涉及 GUI 時執行 `pnpm build` 與 `pnpm test`。
7. 涉及 Windows path／junction／encoding 時保留 native Windows CI 驗證。
8. 驗收完成後確認舊 monolith 已刪除，再進入下一階段。

## 完成定義

- 所有列出的巨大檔案已拆成單一責任模組。
- 舊 monolith 檔案已刪除，或明確縮成必要的薄入口。
- 所有引用、測試、CLI help、GUI bridge 與 plugin-facing 行為均已更新。
- 使用者明確確認後，才把本文件狀態由 Draft 改為 Done。
