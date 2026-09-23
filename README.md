# ai-config

Cross-AI CLI configuration manager and sync engine for Claude Code, Codex, and
Antigravity.

The public tool repository contains the CLI, installers, and tests. Your private
configuration lives in a separate Git repository at the location selected during
setup. A source checkout is optional, and neither repository must be nested
inside the other.

## Installation

### Standalone installer

The released CLI is a single executable with its Python runtime bundled. The
target machine only needs Git; it does not need Python, pip, pipx, or a tool
repository checkout.

Bash — Linux, macOS, Git Bash, MSYS2, or Cygwin:

```bash
curl -fsSL https://raw.githubusercontent.com/CSL426/ai-config/main/install.sh | bash
```

On Windows, the shell installer delegates to the native PowerShell installer
automatically. The native installer also installs `acg.cmd` for PowerShell and
extensionless `ai-config`/`acg` launchers beside `ai-config.exe` for Git Bash.

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/CSL426/ai-config/main/install.ps1 | iex
```

Installers register tab completion for commands, tools, and setup options.
Restart the terminal after installation, then try `ai-config <Tab>` or
`ai-config status <Tab>`. To activate a new or updated install immediately in
the current Bash process, run:

```bash
hash -r && source ~/.local/share/bash-completion/completions/ai-config.bash
```

With Bash's default Readline settings, press Tab twice to list ambiguous
choices such as `pull` and `push`; a unique prefix such as `acg pus<Tab>`
expands immediately.

To print the generated scripts directly, run `ai-config completion bash` or
`ai-config completion powershell`.

When no usable data repository is already configured, the installer starts
first-run setup in an interactive terminal. Setup asks where the private data
repository should live and, when needed, asks for its Git URL. It clones or
opens that repository, checks the required layout, creates and verifies a unique
temporary remote branch, then deletes it. The local path is saved only after the
real push and cleanup both succeed and the remote refs are confirmed restored.
If input is redirected, run `ai-config setup` after installation.

Non-interactive setup uses the same verification:

```bash
ai-config setup \
  --data-dir <path-to-config-repo> \
  --repo-url <your-config-repo-url>
ai-config status
```

The persisted path is stored in the platform user configuration directory:

- Linux: `${XDG_CONFIG_HOME:-~/.config}/ai-config/config.json`
- macOS: `~/Library/Application Support/ai-config/config.json`
- Windows: `%APPDATA%\ai-config\config.json`

`AI_CONFIG_REPO` remains the highest-priority runtime override. Repository URLs
containing embedded HTTP credentials are rejected; use SSH or the Git credential
manager instead.

### Installer automation

The installer can immediately run non-interactive setup after downloading the
binary:

```bash
curl -fsSL https://raw.githubusercontent.com/CSL426/ai-config/main/install.sh | \
  AI_CONFIG_REPO_URL=<your-config-repo-url> \
  AI_CONFIG_DATA_DIR=<path-to-config-repo> bash
```

Set `AI_CONFIG_VERSION` to install a specific release tag. `AI_CONFIG_BIN_DIR`
overrides the binary destination.

For a GitHub release installed with `uv tool install`, `acg update` uses uv
to install the latest release tag while keeping the tool and executable
directories. `acg update 1.0.39` installs that specific release, including a
downgrade. This does not update or apply the private configuration repository.

### Development install

Contributors working from a source checkout may still use an editable Python
installation:

```bash
python -m venv .venv
.venv/bin/pip install --editable .
```

If an old editable launcher shadows the standalone command, `update`
automatically delegates to the installed standalone executable instead of
requiring PATH or pyenv cleanup first.

## CLI usage

```bash
ai-config <command> [tool] [--force]
```

`--force` (`-f`) answers every `[y/N]` confirmation with yes, so an agent or a
script with no terminal can run `pull`, `apply` and `push` end to end. `reset`
ignores it: deleting every config file still has to be confirmed by a person.
A command that stops because it never got its confirmation exits non-zero.

- `init [tool]` — Gather local configs into the data repository.
- `apply [tool]` — Deploy configuration from the data repository.
- `status [tool]` — Preview repository-to-live differences.
- `pull [tool]` — Fetch and fast-forward a clean data repository, then show
  status without applying. Pull refuses dirty, detached, upstream-less, ahead,
  diverged, or in-progress Git states instead of entering conflict resolution.
- `push [tool]` — Gather local settings, show the repository diff, and ask before
  committing and pushing. If a previous push left reviewed commits ahead of the
  upstream, show and confirm those commits again without gathering or creating
  another commit. If `init` already collected unstaged changes entirely within
  the selected tools, review and publish those exact changes without gathering
  again. Push refuses pre-staged, out-of-scope, detached, behind, diverged, or
  upstream-less states, and cancels if the reviewed state changes.
- `sync [tool]` — Alias for `pull`.
- `project [tool]` — Project local Claude settings to other targets.
- `list` — List all managed tools.
- `package [skill]` — Zip a shared skill (`claude/shared/{both,agy,codex}`) for
  manual upload to Claude Desktop (Settings > Customize > Skills). Claude
  Desktop has no writable local skills directory, so this is a one-way export,
  not a live sync.
- `deploy [dir]` — Copy managed Claude configuration into a project's own
  `.claude/` directory rather than the user home. Lists the available entries,
  asks which to take (numbers, a range, or `a` for all), previews the
  destinations, and names anything it would overwrite before writing. Project
  settings take precedence over user-level configuration, so this pins a
  project's setup for handoff or CI.
- `reset` — Remove managed configuration files after confirmation.
- `skill` — Print the built-in usage guide, written for an AI agent that finds
  this CLI on a machine and needs to know how to drive it: the command table,
  what syncs where, the approval rules, and the known gotchas. The text is
  compiled into the binary, so it works before `setup` and needs no data
  repository — useful on a machine that has nothing configured yet.
- `completion bash|powershell` — Print a shell completion registration script.
- `update [version]` — Compare the installed standalone version with the latest
  release, then download only when an update is available. Native Windows updates
  hand off to PowerShell so the running executable can exit before replacement.
  Pass a version (`1.0.13` or `v1.0.13`) to install that release specifically;
  a pinned version skips the latest-release comparison, so it can also
  downgrade. Malformed versions are rejected before anything is downloaded.
- `version` / `--version` / `-V` — Show the installed version without network
  access. Both command names show the shared `ai-config (acg)` product label.

Supported tools are `claude`, `codex`, `agy`, and `all`.

For a safe cross-machine workflow, pull and inspect before applying:

```bash
acg pull
acg apply
```

To publish this machine's selected settings:

```bash
acg push codex
```

The push command stages the selected tools before review, so new-file contents
are included in the displayed diff. It never force-pushes. If the reviewed
snapshot changes before commit, the push is cancelled and the collected changes
are left unstaged. If the remote changes after preflight, the normal
non-fast-forward rejection leaves the new commit local for manual review.
The proposed conventional commit message is derived locally from staged paths
and supported structured diffs; for example, model-only changes to Claude and
Agy settings produce `chore: update claude and agy model settings`. No AI or
external service is used to compose it.

If `init` was run separately first, `push` accepts its existing unstaged changes
when every changed path belongs to the selected tools. It skips gathering in
that case, preserving the collected snapshot for review. Pre-staged changes,
credential files, out-of-scope paths, or a mixture of dirty changes and
unpublished commits remain blocked.

Rerunning `push` safely resumes that ahead-only state: it scans every local
commit for out-of-scope paths and credential content, shows the commit list and
diff again, then asks before pushing the exact reviewed commit. It does not
gather new live changes or create another commit during a retry. Merge commits
and behind or diverged histories remain manual Git operations.

The pull command never rebases or autostashes. It fetches first and updates the
current branch only when `git merge --ff-only` is safe. Local commits or working
tree changes must be handled before pulling.

Codex settings remain under `~/.codex`, while user Skills are deployed to the
cross-surface `~/.agents/skills` directory used by Codex Desktop, CLI, and the
IDE extension. Antigravity global Skills are deployed to
`~/.gemini/config/skills`.

## Claude Code plugin

This repository is also a plugin marketplace, so commands can carry
an `acg:` prefix that says where they came from:

```bash
claude plugin marketplace add CSL426/ai-config
claude plugin install acg@acg
```

It adds eight slash commands wrapping `acg`: `/acg:status`, `/acg:sync`,
`/acg:save`, `/acg:share`, `/acg:memory`, and three for session handoff
(`/acg:handoff` records where a work thread got to, `/acg:handoffs` lists
waiting threads, and `/acg:pickup` claims one and reads its notes back).
The journal answers what happened in a project; handoff answers where one thread
got to, which matters when several sessions work on the same project at once.

The plugin carries only commands. The `acg` skill itself arrives through
`apply` like every other skill, so it is not bundled here twice.

### Handoff reminders before context compaction

Claude Code can remind you to record a handoff when its context usage reaches
a chosen percentage. Enable it separately on each machine:

```bash
acg memory handoff remind status
acg memory handoff remind enable     # default: 70%
acg memory handoff remind enable 80  # whole-number threshold: 1–99
acg memory handoff remind disable
```

The desktop memory page and `/acg:handoff remind status|enable [percentage]|disable`
manage the same setting. The feature is off by default and is specific to
Claude Code. It reads the official context percentage through a statusLine
wrapper that preserves your existing status-line output. Settings and readings
stay on this machine.

At the threshold, the next prompt submission or completed tool call injects
one reminder per session compaction cycle, even if no thread has been claimed.
Missing, null, or stale readings are skipped. PreCompact only resets the
reminder state; it neither requests a handoff nor blocks compaction. A reminder
does not save notes automatically: use `/acg:handoff` or
`acg memory handoff write` to record progress. See the
[handoff reminder specification](docs/handoff-reminder-spec.md).

## Two GitHub accounts on one machine

`acg login` binds an account to the data repository, but gh's credential helper
only ever answers as the machine-wide active account, so it cannot give a
different identity to a different repository. The fix is an SSH key and a host
alias per account, which bypasses the helper entirely. See
[docs/multiple-github-accounts.md](docs/multiple-github-accounts.md) — it also
explains why `~/.ssh/config` must not be synced.

## Data repository contract

The data repository is the source of truth. `init` gathers live configuration
into it; `apply` deploys its content to the corresponding tool home directories.
The configured repository, or the path overridden by `AI_CONFIG_REPO`, must
contain this layout:

```text
<data-repo>/
├── claude/
│   ├── rules/
│   ├── agents/
│   ├── commands/
│   ├── skills/
│   ├── settings.json
│   └── shared/
├── codex/
│   ├── config.toml
│   └── skills/
└── agy/
    └── settings.json
```

Credential files such as `auth.json` are excluded from synchronization.
Codex top-level `notify` and `[projects.*]` tables are machine-local: `init` and
`status` ignore them, while `apply` preserves the live machine's values.

## Development and testing

```bash
python -m pytest
ruff check ai_config tests
bash -n install.sh
git diff --check
```

### Desktop app (experimental)

A desktop window for people who would rather not use the terminal,
covering status/apply/pull/push.

On Windows the released executable already contains it: download
`acg.exe` from the latest release and double-click it, or run `acg gui`.
The Windows installer puts an `acg` shortcut on the desktop and in the
Start menu on first install (set `AI_CONFIG_NO_SHORTCUT=1` to skip it);
`acg gui --shortcut` adds them again, and on Linux adds an app-menu entry
plus a desktop icon when a Desktop folder exists. Windows 10 builds without
Microsoft Edge WebView2 need that runtime installed first.

Elsewhere, build and install from the same source checkout:

```bash
cd gui && pnpm install && pnpm build # frontend → ai_config/gui_assets/
cd ..
python -m pip install -e ".[gui]"    # this checkout, including its built assets
acg gui
```

On Linux install `pywebview[qt]` in the same Python environment, or provide
the GTK backend's system packages (`gir1.2-webkit2-4.1` and `python3-gi` on
Debian and Ubuntu). Launch from a graphical desktop terminal; a plain SSH
session without X11 or Wayland cannot display the window.

A uv installation from Git does not include the ignored frontend build
output. Building in a separate checkout does not change that installed copy.

Roadmap and design notes: [docs/gui-plan.md](docs/gui-plan.md).

Additional documentation:

- [Architecture](docs/architecture.md)
- [Skills](docs/skills.md)
- [Development](docs/development.md)
- [Platform behavior](docs/platform-behavior.md)
- [Desktop app plan (draft)](docs/gui-plan.md)

## License

MIT
