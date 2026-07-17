# ai-config Contributor Guide

This repository contains the public Python CLI and sync engine. The private
configuration repository is a separate Git checkout at `data/` and must never
be staged or committed here.

## Architecture

- `ai_config/` contains the cross-platform implementation.
- `tests/` contains Linux and native Windows contract tests.
- `install.sh` and `install.ps1` install this checkout in editable mode.
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

## Verification

Run before committing implementation changes:

```bash
python -m pytest
ruff check ai_config tests
bash -n install.sh
git diff --check
```

Native Windows CI on Python 3.11 and 3.12 is required for path, Junction,
PowerShell, encoding, and filesystem timestamp behavior.
