# Changelog

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
