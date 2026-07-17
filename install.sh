#!/usr/bin/env bash
# ai-config bootstrap installer (Linux / Unix)
#
#   全新機器:  git clone <tool-repo-url> ~/ai-config-tool && ~/ai-config-tool/install.sh
#   已有 repo: ~/ai-config-tool/install.sh
#
# 全自動處理:定位系統 Python → 建獨立 venv → editable 安裝 → PATH shim。
# 可用環境變數覆寫:AI_CONFIG_REPO_URL / AI_CONFIG_HOME / AI_CONFIG_VENV
set -euo pipefail

REPO_URL="${AI_CONFIG_REPO_URL:-}"
TARGET="${AI_CONFIG_HOME:-$HOME/ai-config}"
VENV="${AI_CONFIG_VENV:-$HOME/.venvs/ai-config}"
BIN_DIR="$HOME/.local/bin"

step() { printf '\033[0;36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m⚠\033[0m %s\n' "$*"; }
fail() { printf '\033[0;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Running from inside a checkout? Then that checkout is the target.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$script_dir/pyproject.toml" && -d "$script_dir/ai_config" ]]; then
    TOOL_SOURCE="$script_dir"
    step "Using this checkout: $TOOL_SOURCE"
    
    if [[ -n "$REPO_URL" && ! -d "$TARGET/.git" ]]; then
        command -v git >/dev/null 2>&1 || fail "git is required to clone data repository"
        step "Cloning data repository: $REPO_URL → $TARGET"
        git clone "$REPO_URL" "$TARGET"
    fi
else
    fail "install.sh must be run from inside the ai-config tool repository checkout."
fi

# System Python ≥ 3.11
PY=""
for candidate in /usr/bin/python3 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
        && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null
    then
        PY="$(command -v "$candidate")"
        break
    fi
done
[[ -n "$PY" ]] || fail "Python 3.11+ not found"
step "Python: $PY"

step "Creating venv: $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --editable "$TOOL_SOURCE"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/ai-config" "$BIN_DIR/ai-config"
step "Installed shim: $BIN_DIR/ai-config"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in PATH — add to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

step "Done. Try: ai-config status"
