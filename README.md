# ai-config

Cross-AI CLI configuration manager and sync engine for Claude Code, Codex, and
Antigravity.

The public tool repository is the outer checkout at `~/ai-config`. Your private
configuration repository lives inside it at `~/ai-config/data` and is ignored by
the outer Git repository. Each repository keeps its own `.git` directory,
history, remote, and commit lifecycle.

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
automatically.

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/CSL426/ai-config/main/install.ps1 | iex
```

Installers register tab completion for commands, tools, and setup options.
Restart the terminal after installation, then try `ai-config <Tab>` or
`ai-config status <Tab>`. To print the generated scripts directly, run
`ai-config completion bash` or `ai-config completion powershell`.

The installer starts first-run setup when it has an interactive terminal. Setup
asks where the private data repository should live and, when needed, asks for
its Git URL. It clones or opens that repository, checks the required layout,
creates and verifies a unique temporary remote branch, then deletes it. The
local path is saved only after the real push and cleanup both succeed and the
remote refs are confirmed restored. If input is redirected, run
`ai-config setup` after installation.

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

## Repository layout

```text
~/ai-config/                 # optional source checkout for contributors
├── .git/
├── ai_config/               # sync engine
├── tests/
├── install.sh
├── install.ps1
└── data/                    # private configuration repository, Git-ignored
    ├── .git/
    ├── claude/
    ├── codex/
    └── agy/
```

The data repository is the source of truth. `init` gathers live configuration
into it; `apply` deploys its content to the corresponding tool home directories.

## CLI usage

```bash
ai-config <command> [tool]
```

- `init [tool]` — Gather local configs into the data repository.
- `apply [tool]` — Deploy configuration from the data repository.
- `status [tool]` — Preview repository-to-live differences.
- `sync [tool]` — Pull data repository updates and show status.
- `project [tool]` — Project local Claude settings to other targets.
- `list` — List all managed tools.
- `reset` — Remove managed configuration files after confirmation.
- `completion bash|powershell` — Print a shell completion registration script.

Supported tools are `claude`, `codex`, `agy`, and `all`.

## Data repository contract

The configured data repository, or the path overridden by `AI_CONFIG_REPO`,
must contain this layout:

```text
data/
├── claude/
│   ├── rules/
│   ├── agents/
│   ├── settings.json
│   └── shared/
├── codex/
│   └── config.toml
└── agy/
    └── settings.json
```

Credential files such as `auth.json` are excluded from synchronization.

## Development and testing

```bash
python -m pytest
ruff check ai_config tests
bash -n install.sh
```

Additional documentation:

- [Architecture](docs/architecture.md)
- [Development](docs/development.md)
- [Platform behavior](docs/platform-behavior.md)

## License

MIT
