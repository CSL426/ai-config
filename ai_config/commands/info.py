"""config command: read-only overview of the active configuration."""

import subprocess
import time

from ..config import ConfigError, config_path, configured_remote_provider
from ..console import log_header, log_info, log_success, log_warn
from ..paths import ALL_TOOLS, SCRIPT_DIR, tilde, tool_home
from ..version import current_version

# 與 sync 的認證遮蔽一致,避免把 URL 內嵌的帳密印出來
from .sync import _GIT_URL_CREDENTIALS


def _repo_git(*args: str) -> "str | None":
    result = subprocess.run(
        ["git", "-C", str(SCRIPT_DIR), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def run_config_info() -> int:
    log_header("Configuration")
    log_info(f"Version: {current_version() or 'unknown'}")
    log_info(f"Config file: {tilde(config_path())}")

    try:
        provider = configured_remote_provider()
    except ConfigError as exc:
        log_warn(f"Config file is invalid: {exc}")
        provider = "git"

    repo_state = "missing"
    if SCRIPT_DIR.is_dir():
        repo_state = "exists"
        if (SCRIPT_DIR / ".git").exists():
            repo_state = "git repository"
    log_info(f"Data repository: {tilde(SCRIPT_DIR)} ({repo_state})")

    provider_label = {
        "git": "git(Git 遠端同步)",
        "gdrive": "gdrive(Google Drive appDataFolder)",
    }.get(provider, provider)
    log_info(f"Remote provider: {provider_label}")

    log_header("Git remote")
    origin = _repo_git("config", "--get", "remote.origin.url")
    branch = _repo_git("symbolic-ref", "--quiet", "--short", "HEAD")
    upstream = _repo_git(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    if origin:
        marker = "✓ 使用中" if provider == "git" else "(閒置,provider 為 gdrive)"
        redacted = _GIT_URL_CREDENTIALS.sub(r"\1***@", origin)
        log_info(f"origin: {redacted} {marker}")
    else:
        log_info("No git remote configured")
    if branch:
        upstream_text = f", upstream {upstream}" if upstream else ", no upstream"
        log_info(f"Branch: {branch}{upstream_text}")

    log_header("Google Drive")
    from ..gdrive import (
        GDRIVE_CLIENT_ID,
        GDriveAuthError,
        get_client_id,
        load_token,
        token_file_path,
    )

    try:
        get_client_id()
    except GDriveAuthError:
        log_warn(
            "Client ID: 不可用(此建置未內建,環境變數 "
            "AI_CONFIG_GDRIVE_CLIENT_ID 也未設定)"
        )
    else:
        source = "built-in" if GDRIVE_CLIENT_ID else "environment variable"
        log_info(f"Client ID: available ({source})")

    token = load_token()
    if token is None:
        log_info(f"Login: not logged in ({tilde(token_file_path())} 不存在)")
    else:
        expires_at = token.get("expires_at", 0)
        remaining = int(expires_at - time.time())
        if remaining > 0:
            state = f"access token 有效,約 {remaining // 60} 分鐘後到期"
        elif token.get("refresh_token"):
            state = "access token 已過期,將用 refresh token 自動更新"
        else:
            state = "已過期且無 refresh token,需重新登入"
        marker = "✓ 使用中" if provider == "gdrive" else "(閒置,provider 為 git)"
        log_info(f"Login: {state} {marker}")

    log_header("Tool homes")
    for tool in ALL_TOOLS:
        home = tool_home(tool)
        mark = "✓" if home.is_dir() else "○"
        log_info(f"{mark} {tool}: {tilde(home)}")

    log_success("Read-only overview; nothing was changed")
    return 0
