# ai-config

Cross-AI CLI configuration manager and sync engine for Claude Code, Codex, and
Antigravity.

The public tool repository is the outer checkout at `~/ai-config`. Your private
configuration repository lives inside it at `~/ai-config/data` and is ignored by
the outer Git repository. Each repository keeps its own `.git` directory,
history, remote, and commit lifecycle.

## Installation

### Recommended layout

Clone the tool repository, then let the installer clone the private data
repository into `data/`:

```bash
git clone <tool-repo-url> ~/ai-config
AI_CONFIG_REPO_URL=<your-config-repo-url> ~/ai-config/install.sh
ai-config status
```

Or clone both repositories explicitly:

```bash
git clone <tool-repo-url> ~/ai-config
git clone <your-config-repo-url> ~/ai-config/data
~/ai-config/install.sh
ai-config status
```

On Windows PowerShell:

```powershell
git clone <tool-repo-url> "$HOME\ai-config"
$env:AI_CONFIG_REPO_URL = '<your-config-repo-url>'
& "$HOME\ai-config\install.ps1"
ai-config status
```

The installers create an isolated virtual environment and install this checkout
in editable mode. `AI_CONFIG_HOME` overrides the data clone destination, and
`AI_CONFIG_VENV` overrides the virtual environment path.

### pipx alternative

The CLI can also be installed directly from Git. The data repository still uses
the same default location:

```bash
pipx install git+<tool-repo-url>
git clone <your-config-repo-url> ~/ai-config/data
ai-config status
```

Set `AI_CONFIG_REPO` when the data repository lives elsewhere:

```bash
export AI_CONFIG_REPO=<path-to-config-repo>
```

## Repository layout

```text
~/ai-config/                 # public tool repository
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

Supported tools are `claude`, `codex`, `agy`, and `all`.

## Data repository contract

The default data repository at `~/ai-config/data`, or the path specified by
`AI_CONFIG_REPO`, must contain this layout:

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
