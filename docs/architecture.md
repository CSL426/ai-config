# Architecture

## Repository boundary

The standard local layout contains two independent Git repositories:

```text
~/ai-config/             public tool repository
├── .git/
├── ai_config/
└── data/                private data repository, ignored by the outer repo
    ├── .git/
    ├── claude/
    ├── codex/
    └── agy/
```

The outer repository owns executable code, installers, tests, and public
documentation. The inner repository owns configuration data only. Each has its
own remote, index, history, and release lifecycle.

## Data repository resolution

The CLI resolves the data repository in this order:

1. `AI_CONFIG_REPO`, expanded and resolved.
2. The Python package parent when it directly contains managed data, for legacy
   fixtures and old combined checkouts.
3. `data/` beneath the tool checkout, for editable installs.
4. `~/ai-config/data`, for installed packages.
5. Legacy `~/ai-config` data layout when managed directories exist there.
6. `~/ai-config/data` as the error-reporting default.

All commands, including `sync`, operate on the resolved data repository.

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

## Safety boundaries

Mutation follows stage, preflight, lock, backup, and apply. A failure before
backup leaves live state untouched. A failure after backup reports the recovery
snapshot. Windows copy fallback records ownership and fingerprints so it cannot
silently claim or overwrite unmanaged content.
