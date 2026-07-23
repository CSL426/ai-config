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
`ai-config status <Tab>`. To print the generated scripts directly, run
`ai-config completion bash` or `ai-config completion powershell`.

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

### Development install

Contributors working from a source checkout may still use an editable Python
installation:

```bash
python -m venv .venv
.venv/bin/pip install --editable .
```

## CLI usage

```bash
ai-config <command> [tool]
```

- `init [tool]` — Gather local configs into the data repository.
- `apply [tool]` — Deploy configuration from the data repository.
- `status [tool]` — Preview repository-to-live differences.
- `pull [tool]` — Fetch and fast-forward a clean data repository, then show
  status without applying. Pull refuses dirty, detached, upstream-less, ahead,
  diverged, or in-progress Git states instead of entering conflict resolution.
- `push [tool]` — Gather local settings, show the repository diff, and ask before
  committing and pushing. If a previous push left reviewed commits ahead of the
  upstream, show and confirm those commits again without gathering or creating
  another commit. Push refuses dirty, detached, behind, diverged, or
  upstream-less repositories, and cancels if the reviewed state changes.
- `sync [tool]` — Alias for `pull`.
- `project [tool]` — Project local Claude settings to other targets.
- `list` — List all managed tools.
- `package [skill]` — Zip a shared skill (`claude/shared/{both,agy,codex}`) for
  manual upload to Claude Desktop (Settings > Customize > Skills). Claude
  Desktop has no writable local skills directory, so this is a one-way export,
  not a live sync.
- `reset` — Remove managed configuration files after confirmation.
- `completion bash|powershell` — Print a shell completion registration script.
- `update` — Compare the installed standalone version with the latest release,
  then download only when an update is available.
- `version` / `--version` / `-V` — Show the installed version without network
  access. Both command names show the shared `ai-config (acg)` product label.

Supported tools are `claude`, `codex`, `agy`, and `all`.

For a safe cross-machine workflow, pull and inspect before applying:

```bash
acg pull
acg apply
```

To publish this machine's selected settings, start from a clean data repository:

```bash
acg push codex
```

The push command stages the selected tools before review, so new-file contents
are included in the displayed diff. It never force-pushes. If the reviewed
snapshot changes before commit, the push is cancelled and the collected changes
are left unstaged. If the remote changes after preflight, the normal
non-fast-forward rejection leaves the new commit local for manual review.

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

Additional documentation:

- [Architecture](docs/architecture.md)
- [Development](docs/development.md)
- [Platform behavior](docs/platform-behavior.md)

## License

MIT
