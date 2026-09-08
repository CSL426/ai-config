"""Built-in usage guide, emitted for AI agents that discover the CLI."""

from .paths import ENTRYPOINT

_GUIDE = """---
name: acg
description: Sync and version-control AI CLI configuration across machines and
  across tools (Claude Code, Codex, Antigravity). Use when config is missing on a
  machine, when a rule/skill/agent must propagate, or when setting up a new box.
---

# acg ({entrypoint})

Cross-AI configuration manager. A public tool repo holds this CLI; a separate
private data repo holds the configuration. **The data repo is the source of
truth**: `init` pulls live config INTO it, `apply` pushes config OUT to the tool
home dirs.

Round trip: edit -> `init` -> commit (or `push`) -> other machine `pull` ->
`apply`.

## Commands

`tool` is `claude` | `codex` | `agy` | `all` (default), accepted by init, apply,
project, status, pull, push, and sync.

| command | does |
|---|---|
| `status [tool]` | diff repo vs live config (read-only, safe anytime) |
| `init [tool]` | gather live config from home dirs INTO the repo |
| `apply [tool] [--category settings|skills|all]` | deploy selected config OUT to home dirs (auto-backs up first) |
| `project [tool]` | project live ~/.claude/ straight to Codex/agy |
| `pull [tool]` | fast-forward the data repo, then show status |
| `sync [tool]` | alias for pull |
| `push [tool]` | gather, review, commit, and push (see guards below) |
| `deploy [dir]` | copy managed Claude config into a project's `.claude/` (interactive) |
| `deploy --profile <name>` | replay a saved selection without prompting |
| `deploy --save-as <name>` | deploy interactively, then remember the selection |
| `list` | managed tools, file counts, backup snapshot count |
| `package [skill]` | zip a shared skill for Claude Desktop upload |
| `setup` | configure the data repo remote and verify push access |
| `update [version]` | install the latest release, or a pinned one (also downgrades) |
| `skill` | print this guide |
| `completion` | print the Bash or PowerShell completion script |
| `reset` | wipe configs to an empty skeleton (confirms first) |

## What syncs where

| repo subdir | tool | home dir |
|---|---|---|
| `claude/` | Claude Code | `~/.claude/` |
| `codex/` | Codex CLI | `~/.codex/`, skills to `~/.agents/skills/` |
| `agy/` | Antigravity | `~/.gemini/antigravity-cli/`, skills to `~/.gemini/config/skills/` |

For Claude, the managed set is `CLAUDE.md`, `settings.json`, `mcp.json`,
`statusline.sh`, and the `rules/ agents/ commands/ skills/` directories.

Skills have two separate mechanisms. `claude/skills/` mirrors verbatim to
Claude Code. `claude/shared/{{both,codex,agy}}/` projects to the other CLIs with
frontmatter normalized for their stricter parsers. A skill wanted everywhere
needs a copy in both. The `shared/` copy is authoritative — deleting it there
removes the mirror on the next apply.

`status` also lists skill directories that exist in a tool's live skills
directory but were never deployed by ai-config (hand-installed skills, or
leftovers from an earlier migration). They are reported only: `apply` never
prunes them, because deleting a skill the user installed themselves would be
worse than leaving a stale one. Remove any you no longer want by hand.

`apply` targets the user home directories. `deploy` targets one project's
`.claude/` instead, for handing a project to someone else or pinning its setup
for CI; project settings take precedence over the user-level ones.

The `deploy` menu lists skills one per row (`skills/acg`), so a project can take
only the skills it needs; the other managed directories stay whole. A selection
worth repeating can be saved with `--save-as <name>` and replayed later with
`--profile <name>`, which skips both prompts. Profiles live in
`deploy-profiles.toml` in the data repository, so they sync between machines.

## Rules for an agent driving this CLI

- **Run `status` first.** It is read-only and shows exactly what would change.
- **Never commit or push without explicit user approval.** This includes
  `push`, which commits as part of its flow.
- **Do not guess the data repo URL or path.** They differ per person; ask.
- `apply` overwrites live config from the repo. If the machine has local edits
  worth keeping, `init` them first.

`apply --category settings` includes rules, commands, MCP, and tool-managed
plugin settings. `apply --category skills` includes standalone skills and
agent-to-skill projections; plugin-bundled skills follow plugin settings.
The option may precede or follow the tool. Omission means `all`, which includes
settings and skills, but never memory. Settings apply preserves the local acg
memory instruction block; use `memory enable` or `memory disable` to change it.
Pull still downloads the entire repository, including shared memory.

## Gotchas

- **`permissions`, `env`, `model`, `modelSettings`, and `autoMode` are
  machine-local and never synced**
  (plus `trustedWorkspaces` on agy). Each machine keeps its own allowlist and
  environment, so a difference there is expected, not
  drift. `env` matters most: Claude Code sets those variables without a shell,
  so a value like `CODEX_HOME=~/.codex` is not expanded and only an absolute
  path works — which cannot be portable. A machine with no `env` block keeps
  none. `model` is switched freely in the UI, so syncing it would undo the
  current choice on every apply.
- **A remote that cannot be pushed to is still a valid setup.** `setup`
  requires read access and treats missing push access as a warning, leaving
  the machine able to run `status`, `pull`, and `apply`. `push` then refuses
  up front instead of failing against the remote.
- **Codex `[projects.*]` and top-level `notify` are preserved** on the target
  machine; apply updates only general settings.
- **`push` refuses to run with pre-staged changes** so it cannot commit an
  unreviewed diff. It also aborts on detached HEAD or an in-progress
  merge/rebase, and rolls back if the staged tree changes mid-flight. On cancel,
  changes are left unstaged, not lost.
- **A `-` line means the file exists only live and `apply` would delete it.**
  Run `init` first if that content is worth keeping.
- **Skill sync requires 1.0.13+.** Older binaries silently skip `skills/`.
- **Credentials are never copied.** `.credentials.json`, `auth.json`,
  `oauth_creds.json`, `google_accounts.json`, and `trustedFolders.json` are
  always excluded.
- Backups land in `~/.ai-config-backup/<timestamp>/` before every apply and
  project.

## Shared memory

`{entrypoint} memory enable` turns `<data-repo>/memory/` into a notebook every
tool reads: it links `~/.claude/shared-memory` to it, appends an instruction
block to `~/.claude/CLAUDE.md` (and to the data repo's copy so other machines
manage it with memory enable), and drops the same block into `~/.gemini/config/rules/` for
agy. Codex's own AGENTS.md also receives the block; an existing link to
Claude's rules is preserved. The notebook has a global
layer (`MEMORY.md`, `topics/`) and a per-project layer under
`projects/<owner--repo>/`, keyed by the project's `origin` URL so the key is
the same on every machine. Save with `{entrypoint} memory push`; `pull` refuses
while tracked memory has uncommitted edits and says so. Pull updates the
notebook immediately; tool settings and skills still require apply.
`{entrypoint} memory disable` removes the
link and blocks but keeps the notes.

The remember plugin's per-project journal (`.remember/`) is Claude-only and
never leaves the machine. `enable` points the plugin at
`~/.claude/shared-memory/journal/{{slug}}` (local, gitignored) and the block
tells every tool to read the journal's `recent.md`. Run `{entrypoint} memory
adopt` inside a project to move that project's journal to
`projects/<owner--repo>/journal/` so it syncs with the notebook; `release`
undoes it.

## Installing on a new machine

```bash
curl -fsSL https://raw.githubusercontent.com/CSL426/ai-config/main/install.sh | \\
  AI_CONFIG_REPO_URL=<git-url> AI_CONFIG_DATA_DIR=<path> bash
hash -r
{entrypoint} status
{entrypoint} apply
```

Windows uses `irm https://raw.githubusercontent.com/CSL426/ai-config/main/install.ps1 | iex`.
Use an SSH URL; URLs with embedded HTTP credentials are rejected. Tracked skills
arrive with `apply` — there is no separate skill install step.

`AI_CONFIG_REPO` overrides the saved data repo path at runtime.
"""


def render_guide() -> str:
    return _GUIDE.format(entrypoint=ENTRYPOINT)
