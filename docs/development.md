# Development

## Local verification

```bash
python -m pytest
ruff check ai_config tests
bash -n install.sh
git diff --check
```

## Standalone build

PyInstaller builds one native executable containing the Python runtime and all
CLI modules:

```bash
python -m pip install pyinstaller==6.21.0
python -m PyInstaller --clean --noconfirm --onefile \
  --name ai-config --hidden-import ai_config.__main__ \
  scripts/standalone_entry.py
dist/ai-config help
```

PyInstaller is not a cross-compiler. `.github/workflows/standalone-release.yml`
builds Linux x86-64, Windows x86-64, macOS Intel, and macOS arm64 assets on
native runners. A `v*` tag publishes the binaries and SHA-256 files as a GitHub
Release; manual workflow runs retain them as CI artifacts without publishing.

The full test suite uses temporary homes and configuration repositories. Tests
must not depend on the developer's nested `data/` checkout.

## CI matrix

GitHub Actions runs Python 3.11 and 3.12 on Ubuntu and Windows. Ubuntu validates
the common implementation and shell installer. Native Windows additionally
validates PowerShell syntax compatibility, Junction and reparse behavior,
filesystem timestamp precision, console encoding, newline conversion, and path
identity across long and 8.3 forms.

## Change guidance

- Add a regression test that reproduces the original failure.
- Prefer platform-neutral behavior in the shared implementation.
- Use filesystem identity for existing Windows paths; textual normalization is
  only a fallback for paths that do not yet exist.
- Use `Path.as_posix()` for user-facing repository-relative paths.
- Compare semantic text after intentional newline normalization, but hash the
  actual bytes when a fingerprint contract requires byte identity.
- Keep status read-only and ensure every apply target passes preflight before
  any selected tool is mutated.

## Public repository hygiene

Before publishing changes, scan tracked content for credentials, private keys,
personal absolute paths, private repository identifiers, hosts, and live service
URLs. The ignored `data/` repository must never appear in the outer index.
