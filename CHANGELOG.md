# Changelog

## 1.0.22 - 2026-08-27

- Accept a data repository the machine can read but not write to. `setup`
  previously verified push access and aborted when it failed, so a machine with
  read-only credentials could not be configured at all, even though `status`,
  `pull`, and `apply` only ever read. Read access is now the requirement and a
  failed push is a warning; `push` checks up front and explains the situation
  rather than failing against the remote.
- Treat `statusLine` and `env` as machine-local, alongside `permissions`. Both
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
