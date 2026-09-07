"""GitHub credential help for the data repository.

A machine can read the data repository but not write to it, which acg
reports as read-only. The cause is almost always one of three things:
no GitHub CLI, signed in as an account without write access, or a token
that git itself never sees. This module tells them apart so the CLI and
the desktop app can say which one it is, and offer the matching fix.

`gh` is used rather than a token of our own: it already owns the login
flow, stores the credential where git looks for it, and refreshes it.
Adding a second credential store would mean two things to keep in sync.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# GitHub OAuth app 的 client ID。開放原始碼儲存庫中為空字串,正式建置由
# GitHub secret 注入,本機開發用 AI_CONFIG_GITHUB_CLIENT_ID 環境變數。
# device flow 不需要 client secret,所以這裡沒有對應的機密。
GITHUB_CLIENT_ID = ""
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEVICE_VERIFY_URL = "https://github.com/login/device"
# repo 涵蓋私有儲存庫的讀寫,這是推送設定所需的最小範圍
DEVICE_SCOPE = "repo"


class GhAuthError(RuntimeError):
    """Raised when a device-flow login cannot be completed."""


def get_client_id(environ: "dict[str, str] | None" = None) -> str:
    environment = os.environ if environ is None else environ
    client_id = environment.get("AI_CONFIG_GITHUB_CLIENT_ID") or GITHUB_CLIENT_ID
    if not client_id:
        raise GhAuthError(
            "此建置未包含 GitHub 登入,請設定 AI_CONFIG_GITHUB_CLIENT_ID 環境變數"
        )
    return client_id


# git@github.com:owner/repo.git · https://github.com/owner/repo · with .git
_GITHUB_REMOTE = re.compile(
    r"github\.com[:/]+(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
# 多帳號常見的 SSH host alias:git@github-work:owner/repo.git。
# HostName 實際指向 github.com,但 URL 裡看不出來,所以另外認。
_GITHUB_ALIAS = re.compile(
    r"^(?:ssh://)?[^@/]+@(?P<host>[A-Za-z0-9._-]*github[A-Za-z0-9._-]*)"
    r"[:/]+(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


@dataclass
class GhStatus:
    """What is known about pushing to this remote, and what would fix it."""

    installed: bool = False
    logged_in: bool = False
    account: str = ""
    repository: str = ""
    can_push: "bool | None" = None
    accounts: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def actionable(self) -> bool:
        """Whether acg can offer to fix this from here."""
        return self.installed and self.can_push is not True


def parse_github_repository(remote_url: str) -> str:
    """Return ``owner/repo`` for a GitHub remote, else an empty string."""
    url = remote_url.strip()
    match = _GITHUB_REMOTE.search(url) or _GITHUB_ALIAS.match(url)
    if not match:
        return ""
    return f"{match['owner']}/{match['repo']}"


def _run_gh(*args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _logged_in_accounts() -> "tuple[str, list[str]]":
    """The active account and every account gh knows, from its own status."""
    result = _run_gh("auth", "status")
    if result.returncode != 0:
        return "", []
    text = result.stdout + result.stderr
    accounts = re.findall(r"Logged in to \S+ account (\S+)", text)
    active = ""
    # "- Active account: true" 跟在它所屬的帳號後面
    for name, tail in zip(accounts, re.split(r"Logged in to \S+ account \S+", text)[1:]):
        if re.search(r"Active account:\s*true", tail):
            active = name
            break
    if not active and accounts:
        active = accounts[0]
    return active, accounts


def check_push_access(remote_url: str) -> GhStatus:
    """Diagnose why a push would be refused, without attempting one."""
    status = GhStatus(repository=parse_github_repository(remote_url))
    if not status.repository:
        status.detail = "遠端不是 GitHub,無法用 gh 處理登入"
        return status
    if shutil.which("gh") is None:
        status.detail = "找不到 GitHub CLI (gh)"
        return status

    status.installed = True
    try:
        status.account, status.accounts = _logged_in_accounts()
    except (OSError, subprocess.SubprocessError) as exc:
        status.detail = f"無法讀取 gh 登入狀態:{exc}"
        return status
    if not status.account:
        status.detail = "gh 尚未登入任何 GitHub 帳號"
        return status

    status.logged_in = True
    try:
        result = _run_gh(
            "api",
            f"repos/{status.repository}",
            "--jq",
            ".permissions.push",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        status.detail = f"無法查詢儲存庫權限:{exc}"
        return status

    answer = result.stdout.strip().lower()
    if result.returncode != 0:
        # 404 也可能是私有 repo 但這個帳號看不到,對使用者是同一件事
        status.can_push = False
        status.detail = f"{status.account} 看不到或無法寫入 {status.repository}"
        return status
    status.can_push = answer == "true"
    status.detail = (
        f"{status.account} 可以寫入 {status.repository}"
        if status.can_push
        else f"{status.account} 對 {status.repository} 沒有寫入權"
    )
    return status


def describe(status: GhStatus) -> list[str]:
    """Lines explaining the situation, ordered most useful first."""
    if not status.repository:
        return [status.detail]
    if not status.installed:
        return [
            "找不到 GitHub CLI (gh),acg 無法代為登入。",
            "安裝後重試:https://cli.github.com",
        ]
    if not status.logged_in:
        return [f"gh 已安裝但尚未登入,需要一個能寫入 {status.repository} 的帳號。"]
    if status.can_push:
        return [
            f"gh 目前登入 {status.account},且可以寫入 {status.repository}。",
            "如果 push 仍失敗,git 可能還沒接上 gh 的憑證。",
        ]
    others = [name for name in status.accounts if name != status.account]
    lines = [f"gh 目前登入 {status.account},但這個帳號對 {status.repository} 沒有寫入權。"]
    if others:
        lines.append(f"gh 也記得這些帳號:{', '.join(others)}")
    return lines


def setup_git_credentials(repository_dir: "Path | None" = None) -> tuple[bool, str]:
    """Point git at gh for GitHub credentials.

    Without this a valid gh login still fails to push: gh holds the token
    but git never asks it for one.
    """
    try:
        result = _run_gh("auth", "setup-git")
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    del repository_dir
    return True, ""


def switch_account(account: str) -> tuple[bool, str]:
    """Make an already-known gh account the active one."""
    try:
        result = _run_gh("auth", "switch", "--user", account)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, ""


def start_device_login(
    environ: "dict[str, str] | None" = None,
) -> dict:
    """Ask GitHub for a code the user types into their browser.

    The device flow exists for exactly this situation: a client that
    cannot host a redirect and has no terminal to prompt in.
    """
    client_id = get_client_id(environ)
    payload = urllib.parse.urlencode(
        {"client_id": client_id, "scope": DEVICE_SCOPE}
    ).encode("ascii")
    request = urllib.request.Request(
        DEVICE_CODE_URL,
        data=payload,
        headers={"Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GhAuthError(f"無法向 GitHub 取得登入碼:{exc}") from exc
    if "device_code" not in data:
        raise GhAuthError(str(data.get("error_description") or data))
    return {
        "device_code": data["device_code"],
        "user_code": data.get("user_code", ""),
        "verification_uri": data.get("verification_uri", DEVICE_VERIFY_URL),
        "interval": int(data.get("interval", 5)),
        "expires_in": int(data.get("expires_in", 900)),
    }


def poll_device_login(
    device_code: str,
    interval: int = 5,
    environ: "dict[str, str] | None" = None,
) -> "str | None":
    """Ask once whether the user has approved yet.

    Returns the token, or None while still pending. Polling one step at a
    time keeps the caller responsive: the desktop app drives the wait and
    can show progress or let the user give up.
    """
    client_id = get_client_id(environ)
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
    ).encode("ascii")
    request = urllib.request.Request(
        ACCESS_TOKEN_URL,
        data=payload,
        headers={"Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GhAuthError(f"無法確認登入狀態:{exc}") from exc

    if token := data.get("access_token"):
        return str(token)
    error = data.get("error", "")
    if error in ("authorization_pending", "slow_down"):
        del interval
        return None
    raise GhAuthError(str(data.get("error_description") or error or data))


def store_token(token: str) -> tuple[bool, str]:
    """Hand the token to gh, which validates it and stores it for git.

    gh rejects an invalid token without disturbing the existing login, so
    a mistyped or expired one cannot lock the user out of what worked.
    """
    if shutil.which("gh") is None:
        return False, "找不到 GitHub CLI (gh)"
    try:
        result = subprocess.run(
            ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
            input=token,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    combined = (result.stderr + result.stdout).strip()
    if result.returncode != 0 or "error" in combined.lower():
        return False, combined or "gh 拒絕了這個 token"
    return True, ""


def login_command() -> list[str]:
    """The interactive login command; it needs a terminal, so it is not run here."""
    return ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https"]


def run_interactive_login() -> tuple[bool, str]:
    """Hand the terminal to `gh auth login`.

    It prompts for a browser code, so it needs a real terminal. Without
    one it would wait for input that can never arrive, which looks like a
    hang; refuse up front and say what to run instead.
    """
    if shutil.which("gh") is None:
        return False, "找不到 GitHub CLI (gh)"
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False, "登入需要互動式終端機,請在 PowerShell 或終端機執行 gh auth login"
    try:
        result = subprocess.run(login_command(), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return result.returncode == 0, ""
