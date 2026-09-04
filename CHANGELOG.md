# Changelog

## Unreleased

- Google Drive 的儲存位置改為可選:`visible`(預設,「我的雲端硬碟」底下看得到的
  資料夾)或 `hidden`(Google 的隱藏應用程式空間,不會出現在檔案列表)。CLI 用
  `--gdrive-space`,互動模式與 Desktop 首次設定都會問。兩者各自使用最小的 scope
  (`drive.file` 與 `drive.appdata`),都只能存取本程式建立的檔案。換位置要重新
  登入一次,兩邊的資料互不相通。
- Desktop 的執行結果加上「複製輸出」按鈕,並明確允許選取文字。先前視窗裡的
  訊息無法複製,遇到錯誤時沒辦法把內容貼給別人求助。clipboard API 在部分平臺
  不可用,會自動退回隱藏 textarea 的複製方式。
- 修正雙擊開啟時 Desktop 開不起來就整個視窗消失的問題:啟動失敗現在會印出
  原因並停在「按 Enter 關閉視窗」。Windows 缺少 Microsoft Edge WebView2 執行期
  時會給出安裝連結,Linux 則提示需要的 WebKit2GTK 套件。
- Release 另外附上 `acg.exe`(與 `ai-config-windows-x86_64.exe` 同一支執行檔),
  給直接從網頁下載的人一個好記的檔名;安裝腳本與 `acg update` 用的原檔名不變。
- 新增 `acg unshare <skill>`:收回 `share` 分享出去的技能,刪掉 repo 裡
  `claude/shared/` 的副本,`~/.claude/skills/` 的原始技能不動;下次 apply 會
  一併清掉 Codex 與 Antigravity 的鏡像。`--from` 可只收回單一目標。Desktop
  的技能區也加了「取消分享」按鈕,先前只能分享出去、無法退回。
- 新增 `acg gui --shortcut`(Windows):在開始選單建立捷徑,不必去
  `~/.local/bin` 找執行檔。
- 用詞統一:面向使用者的文件與訊息一律用 CLI 與 Desktop,不再混用 GUI 與
  「桌面版」;GUI 只留在指令名稱與設定鍵值裡。非 Windows 的執行檔跑 `acg gui`
  時不再叫使用者去 build 不存在的原始碼,改為說明這個平台需要用 pip 安裝。
- Ctrl+C 在提示中要連按兩次才取消:第一次顯示「再按一次 Ctrl+C 取消」並留在
  原地等你回答,兩秒內再按一次才離開,誤觸不會直接中斷流程。取消時原本的清理
  照常執行(例如 push 會把暫存的變更還原成未 staged),也不再丟出中斷例外。

## 1.0.35 - 2026-09-04

- Windows 執行檔改為點兩下就開 GUI:release 版把前端資源與 pywebview 一起打包,
  從檔案總管點兩下直接開視窗,不再閃一下就關。從 PowerShell/cmd 帶參數執行的
  行為完全不變,仍是純 CLI。其他平台的執行檔不含 GUI(需要額外的 GTK/Cocoa
  執行期依賴),點兩下時會說明用法並停在「按 Enter 關閉視窗」。
- GUI:標題列與空狀態加上吉祥物;修正 gui_assets 路徑(gui.py 搬進 commands/
  後指錯目錄)。Windows 與 macOS 的執行檔內嵌應用程式圖示。

## 1.0.34 - 2026-09-03

- Codex 自帶外掛(`@openai-bundled`、`@openai-primary-runtime`,如 documents、
  pdf、browser、visualize)視為機器本地設定:apply 合併 config.toml 時原樣保留、
  init 不收進 repo、status 的 plugin drift 不再列出。新版 Codex 啟動時會自己寫
  這些區塊,之前會造成「apply 刪掉 → Codex 寫回 → 永遠報漂移」。
- 修正 setup 對既有本機 repo(例如 Google Drive 模式建立的、或手動 `git init`
  的)加上 Git remote 後沒有綁 upstream,導致下一步 `acg pull` 直接報「no
  upstream」。現在 setup 會 fetch 一次並把目前分支綁到同名遠端分支;沒有任何
  commit 的空 repo 會直接採用遠端分支(工作區有檔案時不動它,改印出手動指令)。
  push 權限驗證也不再要求本機一定要有 commit。
- Google Drive 同步改存到「我的雲端硬碟」裡看得到的 `ai-config` 資料夾
  (`repo.bundle` 與 `head.json`),不再用隱藏的 appDataFolder;OAuth scope 由
  `drive.appdata` 改為 `drive.file`(仍只能存取本程式建立的檔案)。setup 完成後
  會印出資料夾連結。既有的 gdrive 使用者第一次 pull/push 會被要求重新登入
  (`acg setup --provider gdrive`),然後 push 一次即可;舊的隱藏資料不會自動
  搬移,可在 Drive 設定 → 管理應用程式 清掉。
- Drive 資料夾位置可設定:`acg setup --provider gdrive --gdrive-folder
  Backups/ai-config`(相對於「我的雲端硬碟」,`/` 分層,預設 `ai-config`);互動
  模式與 GUI 首次設定都會問。config.json 記錄 `gdrive_folder` 與
  `gdrive_folder_id`,之後同步以 id 為準,在 Drive 裡搬動或改名資料夾都不會失聯;
  資料夾被刪除時才照路徑重建。`acg config` 顯示資料夾路徑與連結。
- GUI:push 改為兩段式 — 先取得與 CLI 完全一致的審查內容(diff 與 commit
  訊息)顯示給使用者,確認後才送出;不再預填同意。
- 新增對外介紹簡報(docs/slides/intro.html,v1.0.33 內容含 Google Drive/
  Git 雙同步與 GUI 導覽)與 GitHub Pages 自動部署 workflow。

## 1.0.33 - 2026-09-02

- status(以及 pull 結尾的狀態顯示)聚合長清單:「只在 ai-config」的新檔與
  「只在 live」的待刪檔,同目錄超過 3 個就收成一行統計(如
  `+ skills/acg/ (12 files only in ai-config)`);內容有差異的檔案照舊
  逐檔顯示 diff。首次同步不再噴出數百行。

## 1.0.32 - 2026-09-02

- 資料儲存庫的預設位置改為 `~/.acg/data`(隱藏目錄,符合 CLI 慣例)。
  只影響全新 setup;已設定的機器 config 記的是絕對路徑,完全不受影響,
  未設定 config 的機器也仍找得到舊預設 `~/ai-config/data`。
- push 的審查顯示學 git:檔案清單最多列 20 筆(其餘顯示「… and N more」),
  diff 改為 diffstat 摘要;完整 diff 只在 200 行內全印,太長改提示
  `git -C <repo> diff --cached` 查看。審查一致性比對仍用完整 diff,安全性不變。

## 1.0.31 - 2026-08-31

- 修正繁中 Windows(cp950)上 git 輸出的解碼錯誤:diff 內含 UTF-8 中文時
  subprocess 讀取炸掉,push 誤判「沒有 staged 變更」。所有 git 包裝呼叫
  明確指定 UTF-8 解碼(errors=replace)。

## 1.0.30 - 2026-08-31

- push 的空白字元檢查(行尾空白、檔尾空行)降級為警告:同步內容含第三方
  skill 文件,空白風格不再擋 push。

- 新增被動更新提示:一般指令(status/apply/pull/push/init 等)結束時,若快取
  得知有新版就提示「執行 acg update 更新」。檢查本身零延遲 — 只讀本機快取,
  快取超過 24 小時才派分離的背景行程刷新;非 TTY(腳本)不提示,
  `AI_CONFIG_NO_UPDATE_CHECK=1` 可完全關閉。

## 1.0.29 - 2026-08-31

- 修正 gdrive 首次 push 的第二批 unborn HEAD 問題:取消/失敗時的還原
  (`_unstage_tools`)在沒有首個 commit 的 repo 改用 `git reset -- <paths>`。
- 新增 `acg push --allow-secrets`:憑證內容掃描誤判(教學文件、範例金鑰)
  時,檢視清單後可明示放行;預設仍硬擋,GUI 的 push 不受影響。
- 新增 `acg config`:唯讀狀態總覽 — 目前的 provider(git/gdrive)、資料庫
  位置、Git remote(遮蔽帳密)、Google Drive 登入與 token 狀態、各工具
  home 目錄。未完成 setup 也能執行。

## 1.0.28 - 2026-08-31

- 修正 gdrive 首次 push:全新資料庫的 unborn HEAD 讓未提交掃描炸出
  `fatal: bad revision 'HEAD'`;改以「索引 vs 空樹」加「工作樹 vs 索引」
  取得未提交清單,首次 `acg push` 可以順利建立第一個 commit 並上傳。

## 1.0.27 - 2026-08-31

- 修正 Google Drive 登入:Google 的 Desktop 類型 client 在 token 交換時強制要求
  client_secret(即使走 PKCE),新增 `AI_CONFIG_GDRIVE_CLIENT_SECRET` 環境變數與
  release 注入;token 交換失敗時回報 Google 的完整錯誤內容。
- Ctrl+C 改為安靜退出(exit 130),不再觸發 PyInstaller 的未攔截例外視窗。

## 1.0.26 - 2026-08-31

- 新增 Google Drive 同步後端 (`remote_provider: "gdrive"`)：基於 OAuth PKCE 與 `appDataFolder` bundle 傳輸，與預設 Git provider 並列。支援 CLI (`acg setup --provider gdrive`) 與 GUI 介面 Google Drive 設定。

- GUI: add a first-run setup form — when no data repository is configured,
  the window opens with a paste-the-Git-URL form (local directory defaults
  to ~/ai-config/data) instead of failing; sync actions unlock after setup
  (reopen the window once configured).
- Refactor: split the 1800-line `__main__.py` into a `commands/` subpackage —
  apply (apply/init), status, maintenance (list/reset/package/project), sync
  (pull + shared data-repo Git helpers), push, plus the existing share, gui,
  setup, update, and deploy command modules — leaving `__main__.py` as the
  CLI dispatcher. No behavior change.
- GUI: add an update button (check the latest release, then update in place;
  takes effect after reopening the window) and a skill-sharing button — the
  skill list now shows every Claude-side and shared skill with a shared tag,
  and checked skills can be copied into claude/shared/both for Codex and
  Antigravity via the new share machinery.
- Add `acg share <skill> [--to both|codex|agy]`: copy a Claude-side skill —
  from ~/.claude/skills/ or an installed plugin's marketplace copy — into
  claude/shared/ so `apply` projects it to the other CLIs, replacing the
  manual copy step.
- GUI: add a skill-packaging section — check the shared skills to package,
  build the Claude-Desktop-format ZIPs into ~/Downloads, and generate a
  copy-paste installation message for desktop AI assistants that cannot
  install files directly.
- Add an experimental `acg gui` command: a pywebview window aimed at
  non-CLI users, covering status/apply/pull/push with a GUI-side push
  confirmation. The frontend lives in `gui/` (Vite + TypeScript, built into
  `ai_config/gui_assets/`); pywebview installs via the new `ai-config[gui]`
  extra. See `docs/gui-plan.md` for the roadmap (setup wizard, packaging,
  code signing are later phases).

## 1.0.25 - 2026-08-27

- Stop reporting the live Antigravity `plugins/` tree as pending deletions.
  `status` listed every file there as "apply removes" when the repository had
  no `plugins/` of its own, but `apply` mirrors that directory only when the
  repository tracks it and otherwise leaves the live tree alone. Claude is
  unchanged: it really does delete a managed directory the repository dropped.

## 1.0.24 - 2026-08-27

- Stop re-migrating legacy Antigravity skills on every `apply`. The merge from
  `~/.gemini/antigravity/skills` ran unconditionally, so a skill deleted on
  purpose reappeared on the next `apply` as long as the legacy tree still held
  a copy. A marker now records the completed migration, matching Codex. Once
  the legacy directory is gone (or the marker is written) the merge is skipped.

## 1.0.23 - 2026-08-27

- Accept a data repository the machine can read but not write to. `setup`
  previously verified push access and aborted when it failed, so a machine with
  read-only credentials could not be configured at all, even though `status`,
  `pull`, and `apply` only ever read. Read access is now the requirement and a
  failed push is a warning; `push` checks up front and explains the situation
  rather than failing against the remote.
- Treat `statusLine`, `env`, `model`, and `modelSettings` as machine-local,
  alongside `permissions`. `model` is switched freely in the UI, so syncing it
  would undo the current choice on every apply. Both
  name absolute paths belonging to one machine, and a machine with no `env`
  block no longer inherits another's. Claude Code sets those variables without
  a shell, so `CODEX_HOME=~/.codex` is not expanded and only an absolute path
  works — which cannot be portable.

## 1.0.21 - 2026-08-27

- Build and publish `ai-config-linux-aarch64`, and select it from `install.sh`.
  The installer previously refused arm64 Linux outright, because the release
  workflow only built an x86_64 Linux asset, leaving those machines with no
  supported install path. The new asset appears only in releases tagged after
  this change; existing tags are not backfilled.
- List skills individually in the `deploy` menu (`skills/acg`) so a project can
  take only the skills it needs. The other managed directories are still taken
  whole.
- Add `deploy --save-as <name>` to remember a selection and `deploy --profile
  <name>` to replay one without prompting, so a repeated setup can run
  unattended. Profiles live in `claude/deploy-profiles.toml` in the data
  repository and therefore sync between machines.
- Report skill directories in `status` that exist in a tool's live skills
  directory but were never deployed by ai-config — leftovers from the legacy
  Codex migration, or hand-installed skills. They are reported only: `apply`
  still never prunes them.

## 1.0.20 - 2026-08-05

- Leave Codex's own `.system` skills behind when migrating legacy
  `$CODEX_HOME/skills` to `~/.agents/skills`. Codex installs that tree itself and
  refreshes it against a version marker, so migrating it moved vendor content out
  of the directory Codex actually reads and stranded a copy that then went stale.
  Existing strays under `~/.agents/skills/.system` can be deleted; Codex
  reinstalls them in the right place on its next session.

## 1.0.19 - 2026-08-04

- Add `deploy [dir]`, which copies managed Claude configuration into a project's
  own `.claude/` instead of the user home directory. It lists what the data
  repository holds and asks which entries to take — individual numbers, a range,
  or `a` for all — then previews the destinations and names anything it would
  overwrite before writing. Useful for handing a project to someone else or
  pinning its configuration for CI.

## 1.0.18 - 2026-07-26

- Explain why a Windows update shows no progress bar: this process has to exit
  before Windows will release the lock on the running executable, so the
  installer can only run after the handoff. The message now says how long it
  usually takes and how to confirm it finished.
- Write a terminal line to the update log on success or failure, so reading the
  log distinguishes "finished" from "still running".

## 1.0.17 - 2026-07-26

- Normalize line endings before comparing a shared skill's `mirror-hash`. The
  check hashed raw bytes, so a Windows checkout storing the mirror source with
  CRLF endings was reported as stale even though the content matched, and the
  suggested hash would have recorded the CRLF form.
- When a mirror does look stale, note that a freshly pulled machine should run
  `apply` first, since the local source may be the outdated side.
- Build the standalone release on tags only. A release commit lands on main and
  is tagged at the same SHA, so the branch trigger built every release twice.

## 1.0.16 - 2026-07-26

- Report live files in a managed directory the repository does not track yet.
  `status` skipped the whole live tree when the repo side was missing, so
  untracked content — an entire `~/.claude/skills` before it became managed —
  was invisible and `status` claimed no differences.
- Give a freshly replaced Windows executable up to 30 seconds to start, rather
  than 5, and fix the "Updated complete" wording in that path.

## 1.0.15 - 2026-07-26

- Stop the Windows update handoff from writing over the shell prompt. The
  background PowerShell installer now logs to `%TEMP%\ai-config-update.log`
  instead of inheriting the console, and `update` reports where to find it.
- Wait for a freshly replaced Windows executable to start before generating
  completions or probing for existing configuration. A onefile build unpacks its
  Python runtime on first launch, and a transient failure there was reported as
  a completion error and misread as "no configuration", which triggered an
  unnecessary first-run setup.

## 1.0.14 - 2026-07-25

- Add `skill`, which prints a built-in usage guide written for AI agents that
  discover the CLI on a machine. The text is compiled in, so it works before
  `setup` and without a data repository.
- Accept an optional version for `update` (`update 1.0.13` or `update v1.0.13`).
  A pinned version skips the latest-release comparison, so it can downgrade, and
  malformed versions are rejected before any download.

## 1.0.13 - 2026-07-25

- Sync `~/.claude/skills` as a managed directory, so Claude Code skills are
  gathered by `init` and deployed by `apply` like `rules`, `agents`, and
  `commands`. Cross-tool skills continue to use `claude/shared/{both,codex,agy}`.
- Document skill authoring and installation in `docs/skills.md`.

## 1.0.12 - 2026-07-24

- Hand off native Windows updates to a background PowerShell installer and
  retry executable replacement until the running binary releases its lock.

## 1.0.11 - 2026-07-23

- Derive `push` commit messages locally from staged tool paths and changed JSON
  keys, including model-only multi-tool settings updates.
- Delegate `update` from a shadowing source/editable launcher to the installed
  standalone executable.
- Make the Bash activation hint refresh both command hashing and completion in
  the current shell.

## 1.0.10 - 2026-07-23

- Let `push` review, commit, and publish safe unstaged tool changes previously
  collected by `init`, without gathering again or requiring manual Git.
- Continue rejecting pre-staged, out-of-scope, credential, dirty-plus-ahead,
  behind, and diverged repository states.

## 1.0.9 - 2026-07-23

- Let `push` safely review and publish existing ahead-only commits left by a
  failed push, without gathering new settings or creating another commit.
- Scan every retried commit for selected-tool scope and credential content,
  reject merge histories, and revalidate the exact local and upstream refs
  after confirmation.
- Show the same `ai-config (acg)` version label from both command names while
  hiding platform-specific `.exe` suffixes.

## 1.0.8 - 2026-07-23

- Add `version`, `--version`, and `-V` commands for offline installed-version
  checks.
- Harden `push` review by displaying staged new-file contents, rejecting
  out-of-scope or credential-like staged changes, and cancelling when the
  reviewed snapshot changes before commit.
- Make `pull` fail closed and fast-forward-only so dirty, ahead, diverged, or
  in-progress repositories never enter autostash or rebase conflicts.
- Complete Bash and PowerShell command-name completion for `package`, version
  and help flags, with clean `ai-config` and `acg` command names that do not
  expose platform-specific `.exe` suffixes.

## 1.0.7 - 2026-07-22

- Add `acg pull [tool]` and guarded `acg push [tool]` commands for
  cross-machine configuration synchronization. Push refuses unsafe repository
  states and requires diff review plus explicit confirmation before committing.
- Install the short `acg` command and its Bash completion alongside standalone
  releases on every supported platform.
- Check the installed standalone version before downloading an update.
- Keep Codex `notify` runtime paths local during init, status, and apply.

## 1.0.6 - 2026-07-21

- Fix `project`/`apply` hanging (or failing with a permission error) while
  mirroring `~/.claude/plugins` to Antigravity CLI: skip the regenerable
  `cache` directory and per-plugin `.git` checkouts under
  `plugins/marketplaces` instead of mirroring them file by file.

## 1.0.5 - 2026-07-20

- Add `ai-config package [skill]` to zip a shared skill
  (`claude/shared/{both,agy,codex}`) for manual upload to Claude Desktop,
  which has no writable local skills directory.
- Migrate Codex Skills to the canonical cross-surface `~/.agents/skills`
  directory shared by Codex Desktop, CLI, and the IDE extension; migrate
  Antigravity global Skills to `~/.gemini/config/skills`.
- Improve standalone installer update behavior.

## 1.0.4 - 2026-07-20

- Add `acg` as a short alias entrypoint, with tab completion and
  invoked-name-aware usage output.
- Keep Claude and Antigravity permission allowlists local to each machine
  during init, apply, and status.
- Add `ai-config update` to download and install the latest standalone
  release in place.

## 1.0.3 - 2026-07-18

- Make the shell installer work from Git Bash, MSYS2, and Cygwin by delegating
  to the native PowerShell installer.
- Keep Antigravity `trustedWorkspaces` local to each machine during init,
  apply, and status.
- Install Bash and PowerShell tab completion for commands, tools, and setup
  options.

## 1.0.2 - 2026-07-17

- Publish standalone releases with explicit GitHub repository context.
- Add a manual recovery input for publishing an existing release tag.

## 1.0.1 - 2026-07-17

- Make the standalone release test gate UTF-8-safe on Windows.
- Handle Windows repository-root path identity correctly.
- Keep missing-setup guidance independent of the platform entrypoint.

## 1.0.0 - 2026-07-17

- Ship standalone Linux, Windows, and macOS executables that do not require a
  target-machine Python installation.
- Add first-run data repository setup with persistent cross-platform paths.
- Verify real remote write access by creating, checking, and removing a unique
  temporary branch before saving configuration.
- Add checksum-verifying release installers and gated multi-platform GitHub
  Release automation.
