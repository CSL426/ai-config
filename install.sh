#!/usr/bin/env bash
# Install the standalone ai-config release. Python is not required.
set -euo pipefail

REPOSITORY="${AI_CONFIG_TOOL_REPOSITORY:-CSL426/ai-config}"
VERSION="${AI_CONFIG_VERSION:-latest}"
BIN_DIR="${AI_CONFIG_BIN_DIR:-$HOME/.local/bin}"
LOCAL_BINARY="${AI_CONFIG_BINARY_PATH:-}"
DATA_REPO_URL="${AI_CONFIG_REPO_URL:-}"
DATA_DIR="${AI_CONFIG_DATA_DIR:-${AI_CONFIG_HOME:-}}"

step() { printf '\033[0;36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m⚠\033[0m %s\n' "$*"; }
fail() { printf '\033[0;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

platform="$(uname -s)"
architecture="$(uname -m)"

case "$platform" in
    MINGW*|MSYS*|CYGWIN*)
        command -v curl >/dev/null 2>&1 || fail "curl is required to download ai-config"
        powershell_command="$(command -v powershell.exe || command -v pwsh.exe || true)"
        [[ -n "$powershell_command" ]] || fail "PowerShell is required to install ai-config on Windows"
        command -v cygpath >/dev/null 2>&1 || fail "cygpath is required to install ai-config from this shell"
        temporary_dir="$(mktemp -d)"
        trap 'rm -rf "$temporary_dir"' EXIT
        powershell_installer="$temporary_dir/install-ai-config.ps1"
        curl --fail --location --silent --show-error \
            "https://raw.githubusercontent.com/$REPOSITORY/main/install.ps1" \
            --output "$powershell_installer"
        windows_installer="$(cygpath -w "$powershell_installer")"
        step "Windows POSIX shell detected; delegating to PowerShell installer"
        "$powershell_command" -NoProfile -ExecutionPolicy Bypass -File "$windows_installer"
        exit
        ;;
esac

case "$platform:$architecture" in
    Linux:x86_64|Linux:amd64) asset="ai-config-linux-x86_64" ;;
    Darwin:x86_64|Darwin:amd64) asset="ai-config-macos-x86_64" ;;
    Darwin:arm64|Darwin:aarch64) asset="ai-config-macos-arm64" ;;
    *) fail "Unsupported platform: $platform $architecture" ;;
esac

mkdir -p "$BIN_DIR"
destination="$BIN_DIR/ai-config"
operation="Installation"
binary_verb="Installed"
if [[ -e "$destination" || -L "$destination" ]]; then
    operation="Update"
    binary_verb="Updated"
fi

install_binary() {
    local staged_binary="$destination.new.$$"
    install -m 755 "$1" "$staged_binary"
    mv -f "$staged_binary" "$destination"
}

install_acg_alias() {
    local alias_path="$BIN_DIR/acg"
    local staged_alias="$alias_path.new.$$"
    if [[ -d "$alias_path" && ! -L "$alias_path" ]]; then
        warn "Not replacing directory used by acg alias: $alias_path"
        return
    fi
    ln -s "ai-config" "$staged_alias"
    mv -f "$staged_alias" "$alias_path"
}

install_bash_completion() {
    local completion_root completion_dir completion_file acg_completion_file
    local staged_completion
    [[ "${AI_CONFIG_SKIP_COMPLETION:-}" == "1" ]] && return
    completion_root="${BASH_COMPLETION_USER_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion}"
    completion_root="${completion_root%%:*}"
    completion_dir="$completion_root/completions"
    completion_file="$completion_dir/ai-config.bash"
    acg_completion_file="$completion_dir/acg.bash"
    staged_completion="$completion_file.new.$$"
    mkdir -p "$completion_dir"
    if "$destination" completion bash > "$staged_completion"; then
        mv -f "$staged_completion" "$completion_file"
        install -m 644 "$completion_file" "$acg_completion_file"
        step "Installed Bash completion: $completion_file"
        step "Activate in this shell: hash -r && source \"$completion_file\""
    else
        rm -f "$staged_completion"
        warn "Shell completion could not be installed"
    fi
}

has_existing_configuration() {
    "$destination" list >/dev/null 2>&1
}

if [[ -n "$LOCAL_BINARY" ]]; then
    [[ -f "$LOCAL_BINARY" ]] || fail "Local binary not found: $LOCAL_BINARY"
    step "Installing local standalone binary"
    install_binary "$LOCAL_BINARY"
else
    command -v curl >/dev/null 2>&1 || fail "curl is required to download ai-config"
    temporary_dir="$(mktemp -d)"
    trap 'rm -rf "$temporary_dir"' EXIT
    if [[ "$VERSION" == "latest" ]]; then
        base_url="https://github.com/$REPOSITORY/releases/latest/download"
    else
        base_url="https://github.com/$REPOSITORY/releases/download/$VERSION"
    fi
    step "Downloading $asset"
    curl --fail --location --silent --show-error \
        "$base_url/$asset" --output "$temporary_dir/$asset"
    curl --fail --location --silent --show-error \
        "$base_url/$asset.sha256" --output "$temporary_dir/$asset.sha256"
    expected="$(awk '{print $1}' "$temporary_dir/$asset.sha256")"
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$temporary_dir/$asset" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$temporary_dir/$asset" | awk '{print $1}')"
    else
        fail "sha256sum or shasum is required to verify the download"
    fi
    [[ "$actual" == "$expected" ]] || fail "Downloaded binary checksum mismatch"
    install_binary "$temporary_dir/$asset"
fi

step "$binary_verb: $destination"
install_acg_alias
install_bash_completion
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in PATH — add: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

if [[ -n "$DATA_REPO_URL" || -n "$DATA_DIR" ]]; then
    DATA_DIR="${DATA_DIR:-$HOME/ai-config/data}"
    setup_args=(setup --data-dir "$DATA_DIR")
    if [[ -n "$DATA_REPO_URL" ]]; then
        setup_args+=(--repo-url "$DATA_REPO_URL")
    fi
    "$destination" "${setup_args[@]}"
    step "$operation complete"
elif has_existing_configuration; then
    step "$operation complete; existing data repository configuration preserved"
else
    if [[ -t 0 ]]; then
        step "Starting first-run setup"
        "$destination"
        step "$operation complete"
    elif (test -t 0 </dev/tty) 2>/dev/null; then
        step "Starting first-run setup"
        "$destination" </dev/tty
        step "$operation complete"
    else
        step "$operation complete; next: ai-config setup"
    fi
fi
