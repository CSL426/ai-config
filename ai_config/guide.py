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
| `push [tool]` | gather, review, commit, and push (see guards below); confirms first |
| `deploy [dir]` | copy managed Claude config into a project's `.claude/` (interactive) |
| `deploy --profile <name>` | replay a saved selection without prompting |
| `deploy --save-as <name>` | deploy interactively, then remember the selection |
| `list` | managed tools, file counts, backup snapshot count |
| `keepalive <status\\|enable [HH:MM ...]\\|disable\\|send> [tool]` | anchor this machine's usage window for claude, codex or agy; off by default, settings stay local |
| `package [skill]` | zip a shared skill for Claude Desktop upload |
| `setup` | configure the data repo remote and verify push access |
| `update [version]` | install the latest release, or a pinned one (also downgrades); refuses to start while another update is running |
| `skill` | print this guide |
| `skill guide` | explicit alias for this guide; works before setup |
| `skill add <local-directory>` | install into data repo and Claude home; refuses overwrite |
| `skill remove <name>` | confirm full removal of standalone sources and mirrors; accepts --force |
| `share <name> [--to both\\|codex\\|agy]` | copy an existing Claude/plugin skill into shared sources |
| `unshare <name> [--from both\\|codex\\|agy]` | remove shared sources only; apply updates mirrors |
| `completion` | print the Bash or PowerShell completion script |
| `reset` | wipe configs to an empty skeleton; always confirms, and `--force` does NOT pass it |
| `--force` / `-f` | global: answer every [y/N] with yes (push, deploy, skill remove). Not reset. |

## What syncs where

| repo subdir | tool | home dir |
|---|---|---|
| `claude/` | Claude Code | `~/.claude/` |
| `codex/` | Codex CLI | `~/.codex/`, skills to `~/.agents/skills/` |
| `agy/` | Antigravity | `~/.gemini/antigravity-cli/`, skills to `~/.gemini/config/skills/` |

For Claude, the managed set is `CLAUDE.md`, `settings.json`, `mcp.json`,
`statusline.sh`, and the `rules/ agents/ commands/ skills/` directories.

`claude/skills/` mirrors verbatim to Claude Code and also projects to Codex/agy
with frontmatter normalized for their stricter parsers. Tool-specific
`codex/skills/` and `agy/skills/` are also sources. Projection precedence is
Claude agents, tool-specific skills, Claude skills, shared/both, shared/tool
(later sources win). Shared-only skills need `claude/shared/{{both,codex,agy}}/`.
Projection copies `SKILL.md`, `examples/`, `references/`, `scripts/`, and
`agents/`; other resources remain in the Claude copy.

`status` also lists skill directories that exist in a tool's live skills
directory but were never deployed by ai-config (hand-installed skills, or
leftovers from an earlier migration). They are reported only: `apply` never
prunes them, because deleting a skill the user installed themselves would be
worse than leaving a stale one. Explicitly remove an unwanted standalone skill
with `skill remove <name>` after reviewing its listed paths.

## Installing and removing skills

`{entrypoint} skill add ./my-skill` accepts a local directory containing
`SKILL.md` with `name` and `description` frontmatter fields. The name is a plain
or quoted string of 1-64 ASCII letters, digits, hyphens, or underscores,
starting with a letter or digit; Windows device names are rejected.
Descriptions support plain/quoted text and YAML literal/folded blocks.
The frontmatter name determines the installed directory name.

Add copies the complete directory into `<data-repo>/claude/skills/<name>` and
`~/.claude/skills/<name>`, excluding credential filenames, `.git`, and acg
index files at any depth. Existing destinations are refused even with --force.
It does not download Git URLs/archives or execute scripts. Review local content
before installing: AI tools may follow its instructions or use scripts later.
Then run `status` and `apply --category skills` to deploy to Codex/agy.

`{entrypoint} skill remove my-skill` lists and permanently removes that exact
name from all standalone data sources (`claude/skills`, `codex/skills`,
`agy/skills`, and all three `claude/shared` targets), plus Claude's live store,
Codex's `~/.agents/skills` and legacy `~/.codex/skills`, and Antigravity's
`~/.gemini/config/skills`, `~/.gemini/antigravity-cli/skills`, and
`~/.gemini/antigravity/skills`. It also clears that name from local skill
indexes. This includes unmanaged copies. It leaves plugins, project-local
skills, backups, and other names intact. It refuses removal if a same-name
Claude agent would regenerate the skill; handle that agent separately first.
Missing skills are a successful no-op; declining confirmation or EOF exits 1.

Removal uses the standard confirmation and honors `--force` because it targets
one explicitly named skill, unlike reset's whole-configuration wipe. Force
never bypasses path safety checks. Symlinks/Junctions and special files are
refused, except verified legacy store links to known canonical stores.
Paths are validated before mutation and checked again after confirmation.
An I/O failure exits non-zero; consult printed removals for partial progress.
For shared-only removal, keep using `unshare <name>` then `apply`; standalone
sources remain, so a same-name standalone skill will still be projected.
These commands do not commit or push; review the data repo changes separately.

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
- **With no terminal, add `--force`.** Confirmations read stdin, so EOF counts
  as no and the command stops with a non-zero exit. `--force` answers them
  instead, which is what lets `pull` -> `apply` -> `push` run unattended. It
  does not reach `reset`, whose confirmation is deliberately out of its scope.
  Exit codes tell the two apart: refused confirmation is non-zero, nothing to
  do is zero.
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

- **`permissions`, `env`, `model`, `modelSettings`, `effortLevel`, and `autoMode` are
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
- **Codex `[projects.*]`, `[hooks.state.*]` and top-level `notify`, `model` and
  `model_reasoning_effort` are preserved** on the target machine; apply
  updates only general settings. Which model a machine runs is picked
  there, like Claude's `model`.
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

## Saving memory unattended

`{entrypoint} memory autopush enable [hour]` registers a daily run with the
platform's own scheduler: a systemd user timer on Linux (with `Persistent=true`,
so a machine that was off catches up), a LaunchAgent on macOS, a scheduled task
on Windows. Default hour is 04:00. Everything it writes lives under the user's
home and `autopush disable` removes it.

The run calls `memory push --if-stale 12`, which stops before touching git when
the notebook has not changed or when a push happened within the last 12 hours.
Machines with no schedule get the same thing opportunistically at the end of any
acg command; set `AI_CONFIG_NO_AUTOPUSH=1` to suppress that. Both paths skip the
confirmation but keep the credential check, so a secret still blocks the push.

## Slash commands

Installing this repository as a plugin (`claude plugin marketplace add
CSL426/ai-config`, then `claude plugin install acg@acg`) adds eight slash
commands whose `acg:` prefix says where they came from: `/acg:status`,
`/acg:sync`, `/acg:save`, `/acg:share`, `/acg:memory`, `/acg:handoff`,
`/acg:handoffs`, `/acg:pickup`. They wrap the CLI below and add no behaviour
of their own, so the guards described here apply to them unchanged. The
plugin carries only commands; the `acg` skill arrives through `apply`.

## Handing a thread to the next session

The journal answers "what happened in this project"; every session in a
project appends to the same files, so it cannot answer "where did my
thread get to". A handoff is per-thread: `{entrypoint} memory handoff
write "<thread>" "<where it got to, what is next>"` before a session
ends, `handoff list [path]` to see what is waiting (a path reads another
project's threads), `handoff claim [thread]`
to take one over (with no name, the one thread this session's name left
— the name survives `/clear`, so handoff, `/clear`, pickup needs no
choosing), `handoff done <thread>` when it is finished. A note is
free text, but `## Goal / State / Verified / Refuted / Unknowns / Next`
headings let `list` show what is still open instead of the first line,
and keep what was checked apart from what was guessed. Claiming
records the session id, so a second session is told who holds it rather
than silently taking it, and a finished thread is refused rather than
resurrected. A thread closed more than a month ago moves to
`handoff/archive/` the next time the list runs, keeping the record out
of the working directory. A claim nobody has touched for a day is taken over
instead, naming who held it: sessions end without running `done`, and
refusing on a session id that no longer exists strands the thread. A
thread left untouched that long also lists as `⚠ 可能已過期` — read it
before working from it, because the work in it may have shipped
elsewhere. A finished thread stays on disk as a record but leaves the
list. Writing again reopens a claimed thread, keeping the date it was
first opened, which `list` shows as how long it has been waiting.

### Anchoring the usage window

A five-hour usage window starts at the account's first call of the day, so
where that call lands decides every boundary after it.
`{entrypoint} keepalive enable [HH:MM ...] [tool]` schedules a throwaway
call at chosen times (four by default) to put the boundaries where the day
needs them; `keepalive status` reports every tool, `disable` removes one
schedule, `send` calls once now. The tool is claude, codex or agy, and
claude when left out.
A failed call logs the tool's own error line, such as an exhausted
usage limit, rather than just its exit code. A failed call also anchors
nothing: the next real use starts the window instead, which is how the
boundaries drift off the chosen times.
For claude, `status` also shows the window Claude Code last reported
(`目前視窗 09:50–14:50`) and warns when it did not start at a scheduled
time: the call then landed inside a window something else on the account
had opened earlier — another machine, the web or the phone. The reset
time is recorded by the status-line wrapper, so it needs the handoff
reminder enabled.

Each tool is anchored separately, with its own times, schedule and log:
three accounts, three windows, no reason for their boundaries to line up.
Every call uses the weakest model and the least thinking that tool offers,
because the call exists to have happened. Off unless a machine turns it
on, and settings stay local — each machine keeps different hours, so
syncing them would have one machine's answer overwrite another's. The
window belongs to the account rather than the machine, so one machine
anchoring it is enough. A machine still running `claude-scheduler` is told
to remove it before claude is enabled, since both anchor that window and
would each fire.

### Machine-local hooks

`{entrypoint} hooks list` shows the Claude Code hooks acg can install here
and which are on; `hooks enable <name>` / `disable <name>` change one. They
are per-machine by nature — each names this machine's own executable — so
gather strips every acg-owned hook out of the database and apply puts the
local ones back. Installing one by hand into settings.json instead is what
sends one machine's paths to every other.

Registered today: `commit-style` refuses a `git commit` subject that is not
`type(scope): description`, handing the reason back so the model rewrites
it. `memory-entry` and `handoff-reminder` are installed by their own
features (`memory enable`, `memory handoff remind enable`) and appear here
for visibility.

### The journal on every host

The project journal only records what remember captures, and Claude Code
was the only host it was installed on. `{entrypoint} memory status` reports
Codex and Antigravity as well; a bare `{entrypoint} memory enable` offers to
install the capture where it is missing, and `{entrypoint} memory enable
codex|agy` / `disable codex|agy` change one host without asking. Codex
reviews new hooks itself: after installing, open codex once and type
/hooks; viewing remember's hooks there records the trust.

### Remind before context compaction (Claude Code only)

`{entrypoint} memory handoff remind status` shows this machine's reminder
configuration. Opt in with `{entrypoint} memory handoff remind enable` (70%),
or `enable 80` to choose a whole-number threshold from 1 to 99. Turn it off
with `{entrypoint} memory handoff remind disable`. The desktop memory page
and `/acg:handoff remind status|enable [percentage]|disable` manage the same
local setting; enabling shared memory does not enable reminders.

The reminder reads Claude Code's official context percentage from its
statusLine input and preserves the existing status-line output. Once usage
reaches the threshold, UserPromptSubmit or PostToolUse adds one reminder per
session compaction cycle. No claimed handoff is required. Missing, null, or
stale readings are skipped. PreCompact only resets the reminder state; it
does not ask the model to write or delay compaction. The reminder itself
never writes a handoff: use `memory handoff write` or `/acg:handoff` to save
the current work and next steps. Reminder settings and readings stay local.

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
adopt` inside a project, or `adopt <path>` from anywhere, to move that
project's journal to `projects/<owner--repo>/journal/` so it syncs with the
notebook; `release` undoes it and takes a path too. `adopt all` (or `--scan [dir]`)
lists every project below a directory (the home directory by default) whose
journal is still local and adopts the lot on one confirmation, since
visiting them one at a time is the reason most stay unsynced. The scan
leaves the home directory and the data repository out: neither is a
project, and adopting the repository would nest the notebook in its own
journal. Local hooks restore the project's `.remember` entry after migration,
so the journal remains visible inside the project without enabling Git sync.
They only replace a missing entry or a verified migration notice; other content
is preserved. Disable removes these hooks and keeps existing entries and data.

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
arrive with `apply`; use `skill add <local-directory>` to introduce a new one.

`AI_CONFIG_REPO` overrides the saved data repo path at runtime.
"""


def render_guide() -> str:
    return _GUIDE.format(entrypoint=ENTRYPOINT)
