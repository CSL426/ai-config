# ai-config Contributor Guide

This repository contains the public Python CLI and sync engine. The private
configuration repository is a separate Git checkout at `data/` and must never
be staged or committed here.

## Architecture

- `ai_config/` contains the cross-platform implementation.
- `tests/` contains Linux and native Windows contract tests.
- `install.sh` and `install.ps1` download a released standalone binary; they
  do not install this checkout.
- `data/` is ignored local state and is not part of this repository.

The data repository is the source of truth. `init` gathers live configuration
into it; `apply` deploys from it. Credentials are excluded from every gather,
projection, backup, and reconciliation path.

## Development rules

- Preserve behavior across Linux and native Windows.
- Keep Python 3.11 compatibility and prefer the standard library.
- Treat symlinks, junctions, reparse points, backup ownership, and path
  containment as security boundaries.
- Keep user-facing relative paths portable by rendering them with `/`.
- Normalize text only where the file contract allows it; preserve raw bytes for
  fingerprints and mirror hashes.
- Do not weaken a safety check merely to make a platform test pass.
- Never add credentials, private repository names, personal paths, hosts, or
  service URLs to this public repository.

## Surfaces

A feature is not done until every surface that exposes it is updated: the
CLI itself, `--help` (`usage()` in `ai_config/__main__.py`), the agent guide
(`ai_config/guide.py`), the desktop app (`gui/` plus its bridge in
`ai_config/gui_api.py`, `ai_config/gui_management.py` and
`ai_config/gui_deploy.py`), and the Claude Code plugin (the `acg` skill and one
`plugin/skills/<subcommand>/` per subcommand, and the `plugin.json` version). Shipping the CLI alone and
waiting to be asked about the rest is the failure this prevents.

## Rules become checks

A correction that can be stated mechanically is a check, not a sentence in
this file. Prefer, in order: design the wrong choice away (a helper, a type),
a ruff rule in `pyproject.toml`, then a contract test that parses the source
(`tests/test_subprocess_encoding.py`, `tests/test_layer_boundaries.py`,
`tests/test_release_contract.py`). Prose here is for what needs judgment.

Silence a ruff finding with a per-line `# noqa: CODE` and the reason, not by
widening `ignore`; the same goes for pyright with `# pyright: ignore[rule]`.
CI pins the ruff and pyright versions; raise them on purpose, in their own
change.

## Verification

Run before committing implementation changes:

```bash
python -m pytest
ruff check ai_config tests
pyright
bash -n install.sh
git diff --check
```

Native Windows CI on Python 3.11 and 3.12 is required for path, Junction,
PowerShell, encoding, and filesystem timestamp behavior.
