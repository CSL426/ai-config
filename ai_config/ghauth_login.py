"""Signing in to GitHub: the device flow, gh accounts, and storing the token.

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
from pathlib import Path

from .subproc import UTF8

# acg 在 GitHub 註冊的 OAuth App(CSL426 帳號下,名稱 acg)的 client ID。
# device flow 只用 client ID,不需要 client secret;ID 本身不是機密,gh 也是
# 把自己的寫死在原始碼裡。AI_CONFIG_GITHUB_CLIENT_ID 環境變數可覆蓋。
GITHUB_CLIENT_ID = "Ov23likUtXAKeAHe9IFf"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEVICE_VERIFY_URL = "https://github.com/login/device"
# repo 涵蓋私有儲存庫的讀寫;read:org 是 gh 收下 token 時要求的最低範圍,
# 少了它 gh 會拒收:「missing required scope read:org」
DEVICE_SCOPE = "repo read:org"


class GhAuthError(RuntimeError):
    """Raised when a device-flow login cannot be completed."""


# 沒有內建瀏覽器登入時,使用者能做的事;CLI 與 Desktop 共用同一句
TERMINAL_LOGIN_HINT = (
    "請在終端機執行 gh auth login 登入有權限的帳號,"
    "再回到這裡重新開啟設定並選「改用 <帳號>」。"
)


def device_login_available(environ: "dict[str, str] | None" = None) -> bool:
    """Whether this build can run the browser login at all."""
    try:
        get_client_id(environ)
    except GhAuthError:
        return False
    return True


def get_client_id(environ: "dict[str, str] | None" = None) -> str:
    environment = os.environ if environ is None else environ
    client_id = environment.get("AI_CONFIG_GITHUB_CLIENT_ID") or GITHUB_CLIENT_ID
    if not client_id:
        raise GhAuthError(
            "此建置未包含 GitHub 登入,請設定 AI_CONFIG_GITHUB_CLIENT_ID 環境變數"
        )
    return client_id


def _run_gh(
    *args: str, timeout: float = 20.0, token: str = ""
) -> subprocess.CompletedProcess:
    # GH_TOKEN 讓 gh 以指定帳號行事,不用切換作用中帳號
    env = {**os.environ, "GH_TOKEN": token} if token else None
    # Windows 的 CreateProcess 只找 .exe;gh 若是 .cmd 包裝(scoop、npm)要先解析
    executable = shutil.which("gh") or "gh"
    return subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        env=env,
    )


def _logged_in_accounts() -> "tuple[str, list[str]]":
    """The active account and every account gh knows, from its own status."""
    result = _run_gh("auth", "status", "--hostname", "github.com")
    if result.returncode != 0:
        return "", []
    text = result.stdout + result.stderr
    accounts = re.findall(r"Logged in to \S+ account (\S+)", text)
    active = ""
    # "- Active account: true" 跟在它所屬的帳號後面
    for name, tail in zip(
        accounts, re.split(r"Logged in to \S+ account \S+", text)[1:]
    ):
        if re.search(r"Active account:\s*true", tail):
            active = name
            break
    if not active and accounts:
        active = accounts[0]
    return active, accounts


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
    """Make an already-known gh account the active one (machine-wide).

    Login flows use this to restore the original active account after gh
    stores a new login. Repository account selection uses binding instead.
    """
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


def active_account() -> str:
    try:
        return _logged_in_accounts()[0]
    except (OSError, subprocess.SubprocessError):
        return ""


def store_token(token: str) -> tuple[bool, str]:
    """Hand the token to gh, which validates it and stores it.

    Returns the account name on success. gh makes a freshly stored
    account active, which would silently change every other repository
    on the machine, so the previously active account is restored.
    gh rejects an invalid token without disturbing the existing login.
    """
    if shutil.which("gh") is None:
        return False, "找不到 GitHub CLI (gh)"
    previous = active_account()
    try:
        result = subprocess.run(
            [
                shutil.which("gh") or "gh",
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--with-token",
            ],
            input=token,
            capture_output=True,
            text=True, **UTF8,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        failure = str(exc)
    else:
        combined = (result.stderr + result.stdout).strip()
        if result.returncode != 0 or "error" in combined.lower():
            failure = combined or "gh 拒絕了這個 token"
        else:
            failure = ""
    account = active_account()
    if previous and previous != account:
        restored, detail = switch_account(previous)
        if not restored:
            return False, f"無法還原 gh 原本的作用中帳號 {previous}:{detail}"
    if failure:
        return False, failure
    if not account:
        return False, "無法確認新登入的 GitHub 帳號"
    return True, account


def login_command() -> list[str]:
    """The interactive login command; it needs a terminal, so it is not run here."""
    return [
        "gh",
        "auth",
        "login",
        "--hostname",
        "github.com",
        "--git-protocol",
        "https",
    ]


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
