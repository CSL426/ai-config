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
        # 跟要裝的執行檔出自同一個 release
        if [[ "$VERSION" == "latest" ]]; then
            powershell_url="https://github.com/$REPOSITORY/releases/latest/download/install.ps1"
        else
            powershell_url="https://raw.githubusercontent.com/$REPOSITORY/$VERSION/install.ps1"
        fi
        curl --fail --location --silent --show-error \
            "$powershell_url" --output "$powershell_installer"
        windows_installer="$(cygpath -w "$powershell_installer")"
        step "Windows POSIX shell detected; delegating to PowerShell installer"
        "$powershell_command" -NoProfile -ExecutionPolicy Bypass -File "$windows_installer"
        exit
        ;;
esac

case "$platform:$architecture" in
    Linux:x86_64|Linux:amd64) asset="ai-config-linux-x86_64" ;;
    Linux:aarch64|Linux:arm64) asset="ai-config-linux-aarch64" ;;
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

# 每個版本放進自己的目錄,PATH 上的名字只是一條連結。正在執行的檔案因此
# 永遠不必被覆寫 — 那在 Windows 根本做不到,在任何平台也讓回滾無路可走。
SHARE_DIR="${AI_CONFIG_SHARE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/ai-config}"
VERSIONS_DIR="$SHARE_DIR/versions"
KEEP_VERSIONS="${AI_CONFIG_KEEP_VERSIONS:-5}"

adopt_existing_binary() {
    # 這個格局之前裝的機器,真正的執行檔就擺在 PATH 上。第一次更新時把它
    # 收進自己的版本目錄,否則往後每次更新都還是在覆寫一個執行中的檔案。
    local existing
    [[ -f "$destination" && ! -L "$destination" ]] || return 0
    existing="$("$destination" version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
    [[ -n "$existing" ]] || return 0
    [[ -e "$VERSIONS_DIR/$existing/ai-config" ]] && return 0
    mkdir -p "$VERSIONS_DIR/$existing"
    install -m 755 "$destination" "$VERSIONS_DIR/$existing/ai-config"
    step "Adopted the existing $existing into $VERSIONS_DIR/$existing"
}

prune_versions() {
    local active keep_count
    active="$(readlink "$destination" 2>/dev/null | sed -E 's#.*/versions/([^/]+)/.*#\1#')"
    keep_count=0
    # 由新到舊保留 KEEP_VERSIONS 個,正在用的那個永遠不刪
    while IFS= read -r dir; do
        [[ -z "$dir" ]] && continue
        local name="${dir##*/}"
        [[ "$name" == "$active" ]] && continue
        keep_count=$((keep_count + 1))
        if (( keep_count >= KEEP_VERSIONS )); then
            rm -rf "$dir"
        fi
    done < <(ls -d "$VERSIONS_DIR"/*/ 2>/dev/null | sort -rV)
}

probe_version() {
    # 版號問下載回來的執行檔自己:VERSION 可能是 "latest",那時還不知道是哪一版
    local resolved
    chmod +x "$1" 2>/dev/null || true
    resolved="$("$1" version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
    [[ -n "$resolved" ]] || resolved="$(printf '%s' "$VERSION" | sed 's/^v//')"
    # 問不出版號就用一個確定的名字,而不是讓整個安裝失敗。下一次裝得出版號的
    # 更新會把它換掉,而使用者手上至少有一個能跑的執行檔
    [[ -n "$resolved" && "$resolved" != "latest" ]] || resolved="unversioned"
    printf '%s' "$resolved"
}

activate_version() {
    # 換連結是原子操作,而且舊版還留在自己的目錄裡,要退回去只是再換一次
    local staged_link="$destination.new.$$" target="$VERSIONS_DIR/$1/ai-config"
    [[ -x "$VERSIONS_DIR/$1/app/ai-config" ]] && target="$VERSIONS_DIR/$1/app/ai-config"
    ln -sfn "$target" "$staged_link"
    mv -f "$staged_link" "$destination"
    prune_versions
}

install_binary() {
    # 舊式的單一執行檔(onefile):本機測試或指定了沒有壓縮檔的舊版時
    local resolved version_root staged_binary
    adopt_existing_binary
    resolved="$(probe_version "$1")"
    version_root="$VERSIONS_DIR/$resolved"
    mkdir -p "$version_root"
    # 同一個版號先前是 onedir 的話,留著 app/ 會蓋過這次裝的檔案
    rm -rf "$version_root/app"
    staged_binary="$version_root/.ai-config.new.$$"
    install -m 755 "$1" "$staged_binary"
    mv -f "$staged_binary" "$version_root/ai-config"
    activate_version "$resolved"
}

install_directory() {
    # onedir:主程式與 _internal 一起放進版本目錄的 app/。整個目錄先在旁邊
    # 備好再改名,中途失敗不會留下半個版本讓啟動時才壞。多一層 app/ 是給
    # 舊版看的:它們切換版本時找 versions/<版號>/ai-config,找不到才會改用
    # 那一版自己的安裝腳本(ai_config/versions.py 的 version_binary)
    local source_dir="$1" resolved version_root staging aside
    [[ -x "$source_dir/ai-config" ]] || fail "No ai-config executable in $source_dir"
    adopt_existing_binary
    resolved="$(probe_version "$source_dir/ai-config")"
    version_root="$VERSIONS_DIR/$resolved"
    staging="$VERSIONS_DIR/.$resolved.staging.$$"
    mkdir -p "$staging"
    cp -R "$source_dir" "$staging/app"
    if [[ -e "$version_root" ]]; then
        aside="$VERSIONS_DIR/.$resolved.old.$$"
        mv "$version_root" "$aside"
        mv "$staging" "$version_root"
        rm -rf "$aside"
    else
        mv "$staging" "$version_root"
    fi
    activate_version "$resolved"
}

install_archive() {
    local unpacked="$temporary_dir/unpacked"
    rm -rf "$unpacked"
    mkdir -p "$unpacked"
    tar -xzf "$1" -C "$unpacked" || fail "Could not unpack $1"
    install_directory "$unpacked/ai-config"
}

download_verified() {
    # 下載並比對 .sha256;下載不到回傳非零,由呼叫端決定要不要退回舊格式
    local name="$1" expected actual
    curl --fail --location --silent --show-error \
        "$base_url/$name" --output "$temporary_dir/$name" 2>/dev/null || return 1
    curl --fail --location --silent --show-error \
        "$base_url/$name.sha256" --output "$temporary_dir/$name.sha256" \
        || fail "Missing checksum for $name"
    expected="$(awk '{print $1}' "$temporary_dir/$name.sha256")"
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$temporary_dir/$name" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$temporary_dir/$name" | awk '{print $1}')"
    else
        fail "sha256sum or shasum is required to verify the download"
    fi
    [[ "$actual" == "$expected" ]] || fail "Downloaded $name checksum mismatch"
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
    if [[ -d "$LOCAL_BINARY" ]]; then
        step "Installing local build directory"
        install_directory "$LOCAL_BINARY"
    elif [[ "$LOCAL_BINARY" == *.tar.gz ]]; then
        [[ -f "$LOCAL_BINARY" ]] || fail "Local archive not found: $LOCAL_BINARY"
        temporary_dir="$(mktemp -d)"
        trap 'rm -rf "$temporary_dir"' EXIT
        step "Installing local build archive"
        install_archive "$LOCAL_BINARY"
    else
        [[ -f "$LOCAL_BINARY" ]] || fail "Local binary not found: $LOCAL_BINARY"
        step "Installing local standalone binary"
        install_binary "$LOCAL_BINARY"
    fi
else
    command -v curl >/dev/null 2>&1 || fail "curl is required to download ai-config"
    temporary_dir="$(mktemp -d)"
    trap 'rm -rf "$temporary_dir"' EXIT
    if [[ "$VERSION" == "latest" ]]; then
        base_url="https://github.com/$REPOSITORY/releases/latest/download"
    else
        base_url="https://github.com/$REPOSITORY/releases/download/$VERSION"
    fi
    step "Downloading $asset.tar.gz"
    if download_verified "$asset.tar.gz"; then
        install_archive "$temporary_dir/$asset.tar.gz"
    else
        # 1.0.99 以前的 release 只有單一執行檔
        step "No archive in this release; downloading $asset"
        download_verified "$asset" || fail "Could not download $asset"
        install_binary "$temporary_dir/$asset"
    fi
fi

step "$binary_verb: $destination"
install_acg_alias
install_bash_completion
# 讓 Claude Code 的 /acg 跟上這一版,更新時也修 hook 路徑。由剛裝好的新版
# 來做:發起更新的舊版行程可能已經被換掉,不能再在裡面 import
if [[ -z "${AI_CONFIG_NO_PLUGIN:-}" ]]; then
    "$destination" __claude-plugin || warn "Claude Code /acg was not updated; run: ai-config update"
fi
if [[ "$operation" == "Update" ]]; then
    "$destination" __refresh-hooks || warn "Hook paths were not refreshed; run: ai-config apply"
fi
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
