# ai-config

Cross-AI CLI configuration manager and sync engine for Claude Code, Codex, and Antigravity.

`ai-config` separates code (sync and deployment engine) from data (your personal workflow configurations, skills, agents, and rules). It allows you to manage all your AI assistant profiles and preferences under version control.

## Installation

### Method 1: Using pipx (Recommended)

Install the tool globally using `pipx`:

```bash
pipx install git+https://github.com/<your-organization>/ai-config.git
```

To bind a configuration repository immediately, clone your private settings repository and apply the settings:

```bash
git clone git@github.com:<your-organization>/myccskills.git ~/ai-config
ai-config apply
```

### Method 2: Bootstrap Installer

Clone the tool repository and run the bootstrap script:

```bash
git clone https://github.com/<your-organization>/ai-config.git ~/ai-config-tool
cd ~/ai-config-tool
./install.sh
```

You can also pass `AI_CONFIG_REPO_URL` to automatically clone your private settings repository:

```bash
AI_CONFIG_REPO_URL="git@github.com:<your-organization>/myccskills.git" ./install.sh
```

## Features

- **Cross-Tool Configuration**: Manage Claude Code (`~/.claude/`), Codex (`~/.codex/`), and Antigravity (`~/.gemini/antigravity-cli/`) settings in one place.
- **Skill Sharing**: Mirror shared skills across all assistants automatically.
- **Safety Checks**: Automatic validation of symlink escapes and repository invariants.
- **Backup & Rollback**: Automatic rolling backup directory structure before applying mutations.

## CLI Usage

After installation, run `ai-config` from any directory:

```bash
ai-config <command> [tool]
```

### Commands

- `init [tool]` — Gather local configs from active AI home directories into the managed repository.
- `apply [tool]` — Deploy configuration profiles from the repository to local AI home directories.
- `status [tool]` — Preview differences between the repository and current local settings.
- `sync [tool]` — Fetch remote repository updates and show status.
- `project [tool]` — Project local Claude settings to other targets.
- `list` — List all managed tools.
- `reset` — Safely purge managed configuration files from active homes.

## Configuration Repository Layout

Your private settings repository (assumed to be located at `~/ai-config` or specified via `AI_CONFIG_REPO`) should follow this layout:

```
~/ai-config/
├── claude/                  # Claude Code config
│   ├── rules/
│   ├── agents/
│   └── settings.json
├── codex/                   # Codex CLI config
│   └── config.toml
├── agy/                     # Antigravity CLI config
│   └── settings.json
└── claude/shared/           # Shared components (e.g. both/agy/codex skills)
```

## Development and Testing

Run pytest from the repository root:

```bash
python -m pytest
```

## License

MIT
