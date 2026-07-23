# Architecture

## Repository boundary

The tool and data repositories are independent Git checkouts. Their locations
are not coupled:

```text
<tool-checkout>/         optional public contributor checkout
├── .git/
├── ai_config/
├── tests/
├── install.sh
└── install.ps1

<data-repo>/             private location selected during setup
├── .git/
├── claude/
├── codex/
└── agy/
```

The tool repository owns executable code, installers, tests, and public
documentation. The data repository owns configuration data only. Each has its
own remote, index, history, and release lifecycle. A nested `data/` checkout is
supported for contributor convenience but is not required.

## Data repository resolution

The CLI resolves the data repository in this order:

1. `AI_CONFIG_REPO`, expanded and resolved.
2. The path saved by `ai-config setup` in the platform user configuration file.
3. The Python package parent when it directly contains managed data, for legacy
   fixtures and old combined checkouts.
4. `data/` beneath the tool checkout, for editable installs.
5. `~/ai-config/data`, for installed packages.
6. Legacy `~/ai-config` data layout when managed directories exist there.
7. `~/ai-config/data` as the error-reporting default.

Frozen standalone executables skip source-checkout detection because their
package files are extracted into a temporary directory at runtime.

## First-run setup

`setup` accepts an existing data repository or clones a configured Git URL. It
requires the selected directory to be the repository root and requires the
managed `claude/` directory. Embedded HTTP credentials are never accepted.

Before persisting the selected path, setup snapshots the remote refs, pushes a
unique temporary branch, verifies its object ID, deletes it, and compares all
remote refs again. This real create/delete cycle proves remote write access in a
way that `git push --dry-run` cannot. A failed check does not write user
configuration. If cleanup fails, setup reports the exact ref and manual removal
command. If setup temporarily added or replaced a remote on an existing
checkout, that remote change is rolled back when validation fails.

All commands, including `pull`, `push`, and the legacy `sync` alias, operate on
the resolved data repository.

Repository synchronization is directional. `pull` updates the repository and
reports live drift without applying it. On a clean branch that matches its
upstream, `push` gathers only the selected tool configuration, displays the
result, and requires confirmation before staging, committing, and performing a
normal non-force push. On a clean ahead-only branch, `push` instead validates
every local commit, displays the exact commit range and diff, and asks before
retrying an explicit non-force push. It never gathers or creates another commit
while retrying. Behind, diverged, merge-commit, credential, and out-of-scope
states fail closed.

An `init` followed by `push` is also supported. When the repository contains
only unstaged changes within the selected tool directories, `push` preserves
that collected snapshot, skips another gather, and proceeds through the same
scope, credential, diff, and confirmation checks. Pre-staged changes and dirty
plus ahead states remain manual Git operations.

Commit messages are deterministic local metadata. The push workflow inspects
staged paths and, for JSON settings, changed top-level keys to produce a
specific conventional message before review, then falls back to a tool-scoped
configuration message when no narrower description is available.

## Projection model

The data repository is authoritative:

- `init` gathers managed live files into data.
- `apply` stages a projection, preflights every selected destination, creates a
  backup, and deploys the projection.
- `status` stages the same projection but remains read-only.
- `project` uses live Claude configuration as a temporary projection source.

Shared skills are projected from `claude/shared/{both,codex,agy}`. Only the
skill document and supported companion directories are managed. Credentials
and unmanaged, hand-installed skills remain outside reconciliation.

Machine-local settings are excluded symmetrically from gather and status, then
preserved from the live target during apply. For Codex this includes the
top-level `notify` command, whose executable path belongs to the installed
runtime, and every `[projects.*]` trust table.

Tool homes and Skill destinations are separate when the upstream product uses
a cross-surface Skill directory. Codex configuration remains in `~/.codex`,
while staged `codex/skills` content deploys to `~/.agents/skills`. Antigravity
CLI configuration remains in `~/.gemini/antigravity-cli`, while its canonical
global Skills live in `~/.gemini/config/skills`.

## Safety boundaries

Mutation follows stage, preflight, lock, backup, and apply. A failure before
backup leaves live state untouched. A failure after backup reports the recovery
snapshot. Windows copy fallback records ownership and fingerprints so it cannot
silently claim or overwrite unmanaged content.
