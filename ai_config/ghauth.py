"""GitHub credential help for the data repository.

A machine can read the data repository but not write to it, which acg
reports as read-only. The cause is almost always one of three things:
no GitHub CLI, signed in as an account without write access, or a token
that git itself never sees. This module tells them apart so the CLI and
the desktop app can say which one it is, and offer the matching fix.

`gh` is used rather than a token of our own: it already owns the login
flow, stores the credential where git looks for it, and refreshes it.
Adding a second credential store would mean two things to keep in sync.

The account is bound to the data repository alone. gh's active account
is never switched: other repositories on the machine keep whatever they
were using. The binding is a repo-local git credential helper that asks
gh for the bound account's token whenever git needs one.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .safety import is_reparse_point

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


HELPER_MARKER = "__git-credential"


@dataclass
class GhStatus:
    """What is known about pushing to this remote, and what would fix it."""

    installed: bool = False
    logged_in: bool = False
    account: str = ""
    repository: str = ""
    can_push: "bool | None" = None
    account_can_push: "bool | None" = None
    accounts: list[str] = field(default_factory=list)
    detail: str = ""
    # 綁在資料庫上的帳號;空字串表示沿用 gh 的作用中帳號或既有金鑰
    bound: str = ""

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


def _run_git(
    repo_dir: Path, *args: str, timeout: float = 30.0
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


# ─── repo-local binding ───────────────────────────────────────


def _acg_command() -> list[str]:
    executable = sys.executable
    if os.name == "nt":
        # git 在 Windows 用它自帶的 sh 執行 helper;反斜線在 sh 裡是跳脫字元,
        # 正斜線的 Windows 路徑兩邊都認得
        executable = Path(executable).as_posix()
    if getattr(sys, "frozen", False):
        return [executable]
    return [executable, "-m", "ai_config"]


def helper_value(account: str) -> str:
    """The credential.helper entry that hands git this account's token.

    git runs a helper starting with ``!`` through the shell, so the
    executable path is quoted; the entry is machine-specific and lives
    only in the repository's local config, never in the synced data.
    """
    # 單引號:sh 不展開 $、反引號與反斜線;Windows 路徑已改成正斜線
    parts = [shlex.quote(part) for part in _acg_command()]
    return "!" + " ".join([*parts, HELPER_MARKER, account])


_BOUND = re.compile(re.escape(HELPER_MARKER) + r" (\S+)\s*$")


def bound_account(repo_dir: "Path | None") -> str:
    if repo_dir is None:
        return ""
    result = _run_git(repo_dir, "config", "--local", "--get-all", "credential.helper")
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        match = _BOUND.search(line)
        if match:
            return match[1]
    return ""


_HELPERS_BACKUP = "acg.previousCredentialHelpers"


def _change_binding(repo_dir: Path, account: "str | None") -> bool:
    """Publish helper changes together, using the same lock as git config."""
    location = _run_git(repo_dir, "rev-parse", "--git-path", "config")
    if location.returncode:
        raise GhAuthError((location.stderr or location.stdout).strip())
    config = Path(location.stdout.strip())
    if not config.is_absolute():
        config = repo_dir / config
    if is_reparse_point(config) or not config.is_file():
        raise GhAuthError("git config 不是一般檔案,無法安全修改綁定")
    lock = config.with_name(config.name + ".lock")
    # An exclusive lock also prevents overwriting another git config writer.
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    stream = os.fdopen(descriptor, "wb")
    try:
        with stream:
            stream.write(config.read_bytes())

        def edit(*args: str, missing_ok: bool = False) -> str:
            result = _run_git(
                repo_dir,
                "config",
                "--file",
                str(lock.absolute()),
                "--no-includes",
                *args,
            )
            if result.returncode and not (missing_ok and result.returncode in (1, 5)):
                raise GhAuthError(
                    (result.stderr or result.stdout).strip() or "無法修改 git 憑證設定"
                )
            return result.stdout

        raw = edit("--null", "--get-all", "credential.helper", missing_ok=True)
        helpers = raw[:-1].split("\0") if raw else []
        saved = edit("--get", _HELPERS_BACKUP, missing_ok=True)
        if saved:
            original = json.loads(saved)
            if not isinstance(original, list) or any(
                not isinstance(value, str) for value in original
            ):
                raise GhAuthError("憑證備份格式不正確,保留目前設定")
        else:
            # Earlier bindings had no backup (a clone with --account writes
            # the helper straight in). Drop our helper and the reset entry
            # before it; everything else is the user's and stays.
            original: list[str] = []
            for value in helpers:
                if _BOUND.search(value):
                    if original and original[-1] == "":
                        original.pop()
                    continue
                original.append(value)
        if account is None and not any(_BOUND.search(v) for v in helpers):
            return False
        if account is not None and not saved:
            edit("--replace-all", _HELPERS_BACKUP, json.dumps(original))
        edit("--unset-all", "credential.helper", missing_ok=True)
        for helper in ["", helper_value(account)] if account else original:
            edit("--add", "credential.helper", helper)
        if account is None:
            edit("--unset-all", _HELPERS_BACKUP, missing_ok=True)
        shutil.copymode(config, lock)
        os.replace(lock, config)
        return True
    finally:
        lock.unlink(missing_ok=True)


def bind_account(repo_dir: Path, account: str) -> tuple[bool, str]:
    """Bind locally, preserving the original helpers until explicit unbind."""
    if not re.fullmatch(r"[A-Za-z0-9-]+", account):
        return False, f"帳號名稱不合法:{account}"
    try:
        remote = _run_git(repo_dir, "remote", "get-url", "--push", "--all", "origin")
        if remote.returncode == 0 and any(
            not url.startswith("https://github.com/")
            for url in remote.stdout.splitlines()
        ):
            return False, (
                "帳號綁定需要 GitHub HTTPS 推送遠端;"
                "SSH 使用既有金鑰,請先調整 origin 的推送 URL"
            )
        _change_binding(repo_dir, account)
    except (OSError, subprocess.SubprocessError, GhAuthError, ValueError) as exc:
        return False, str(exc)
    return True, ""


def unbind_account(repo_dir: Path) -> bool:
    """Restore the original local helpers; failures leave config untouched."""
    try:
        return _change_binding(repo_dir, None)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise GhAuthError(f"無法解除綁定:{exc}") from exc


def account_token(account: str) -> str:
    try:
        result = _run_gh("auth", "token", "--hostname", "github.com", "--user", account)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def credential_helper_main(argv: list[str]) -> int:
    """Entry point git calls: ``acg __git-credential <account> <operation>``."""
    if len(argv) != 2:
        return 1
    account, operation = argv
    if operation != "get":
        return 0
    if sys.stdin.isatty():
        return 0
    request: dict[str, str] = {}
    for line in sys.stdin:
        line = line.rstrip("\r\n")
        if not line:
            break
        key, separator, value = line.partition("=")
        if not separator or key in request:
            return 0
        request[key] = value
    # A repo-local helper can also be asked about other remotes in that repo.
    if request.get("protocol") != "https" or request.get("host") != "github.com":
        return 0
    if not re.fullmatch(r"[A-Za-z0-9-]+", account):
        return 1
    token = account_token(account)
    if not token:
        return 1
    # 走 buffer:Windows 的文字模式會把 \n 換成 \r\n,憑證協定要的是純 LF
    sys.stdout.buffer.write(f"username={account}\npassword={token}\n".encode())
    sys.stdout.buffer.flush()
    return 0


_AUTH_REFUSAL_MARKERS = (
    "denied",
    "permission",
    "403",
    "401",
    "unauthorized",
    "not found",
    "read-only",
    "authentication",
)


def git_push_probe(repo_dir: Path) -> "tuple[bool | None, str]":
    """Whether git itself can push right now, and git's own words if not.

    A dry-run push sends no objects and creates no ref. It is the only
    check that sees the whole picture: SSH keys, bound accounts and
    stored credentials alike. None means the failure was not an
    authentication refusal; the detail says what it was.
    """
    try:
        result = _run_git(
            repo_dir,
            "push",
            "--dry-run",
            "--porcelain",
            "origin",
            "HEAD:refs/heads/main",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if result.returncode == 0:
        return True, ""
    text = (result.stderr or result.stdout).strip()
    first = next((line for line in text.splitlines() if line.strip()), "git push 失敗")
    if any(marker in text.lower() for marker in _AUTH_REFUSAL_MARKERS):
        return False, first
    return None, first


# git 上一次推送測試的錯誤文字;check_push_access 用它把原因說出來。
# 用模組變數而不是回傳值,是為了讓 git_can_push 維持可被測試替換的簡單簽名。
_last_push_detail = ""


def git_can_push(repo_dir: Path) -> "bool | None":
    global _last_push_detail
    verdict, _last_push_detail = git_push_probe(repo_dir)
    return verdict


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


def check_push_access(remote_url: str, repo_dir: "Path | None" = None) -> GhStatus:
    """Diagnose why a push would be refused, without attempting one.

    With ``repo_dir`` the answer starts from what git can actually do:
    a machine whose SSH key already has write access is fine whatever
    gh thinks, and a bound account is judged as itself.
    """
    status = GhStatus(repository=parse_github_repository(remote_url))
    if not status.repository:
        status.detail = "遠端不是 GitHub,無法用 gh 處理登入"
        return status
    status.bound = bound_account(repo_dir)
    global _last_push_detail
    _last_push_detail = ""
    git_access = git_can_push(repo_dir) if repo_dir is not None else None
    git_detail = _last_push_detail if repo_dir is not None else ""
    status.can_push = git_access
    status.installed = shutil.which("gh") is not None
    active = ""
    if status.installed:
        try:
            active, status.accounts = _logged_in_accounts()
        except (OSError, subprocess.SubprocessError) as exc:
            status.detail = f"無法讀取 gh 登入狀態:{exc}"
    status.account = status.bound or active
    status.logged_in = bool(status.account)
    if git_access is True:
        # A successful push does not identify which credential was used;
        # SSH and URL-specific settings can bypass the bound HTTPS helper.
        status.detail = f"git 已可推送到 {status.repository}(依目前遠端與憑證設定)"
        return status
    if not status.installed:
        status.detail = "找不到 GitHub CLI (gh)"
        return status
    if not status.account:
        if not status.detail:
            status.detail = "gh 尚未登入任何 GitHub 帳號"
        return status

    status.logged_in = True
    token = ""
    if status.bound:
        token = account_token(status.bound)
        if not token:
            if repo_dir is None:
                status.can_push = False
            status.detail = f"gh 沒有 {status.bound} 的登入紀錄,資料庫的綁定失效"
            return status
    try:
        result = _run_gh(
            "api",
            f"repos/{status.repository}",
            "--jq",
            ".permissions.push",
            "--hostname",
            "github.com",
            token=token,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        status.detail = f"無法查詢儲存庫權限:{exc}"
        return status

    answer = result.stdout.strip().lower()
    if result.returncode != 0:
        # 404 也可能是私有 repo 但這個帳號看不到,對使用者是同一件事
        if repo_dir is None:
            status.can_push = False
        status.detail = f"{status.account} 看不到或無法寫入 {status.repository}"
        return status
    status.account_can_push = answer == "true"
    if repo_dir is not None and status.account_can_push:
        # 說出 git 自己的錯誤,不然使用者只看到「無法確認」不知道要做什麼
        why = f":{git_detail}" if git_detail else ""
        status.detail = (
            f"{status.account} 有儲存庫寫入權,但 "
            + ("git 憑證驗證失敗" if git_access is False else "git 推送測試沒有成功")
            + why
        )
        return status
    if repo_dir is None:
        status.can_push = status.account_can_push
    status.detail = (
        f"{status.account} 可以寫入 {status.repository}"
        if status.account_can_push
        else f"{status.account} 對 {status.repository} 沒有寫入權"
    )
    return status


def describe(status: GhStatus) -> list[str]:
    """Lines explaining the situation, ordered most useful first."""
    if not status.repository:
        return [status.detail]
    # git 本來就推得動(SSH 金鑰、綁定帳號)時,gh 裝沒裝、登沒登入都不重要
    if status.can_push and status.detail.startswith("git 已可推送"):
        return [status.detail + "。"]
    if not status.installed:
        return [
            "找不到 GitHub CLI (gh),acg 無法代為登入。",
            "安裝後重試:https://cli.github.com(Windows 可用 winget install GitHub.cli)",
        ]
    if not status.logged_in:
        return [f"gh 已安裝但尚未登入,需要一個能寫入 {status.repository} 的帳號。"]
    if status.can_push:
        return [
            f"gh 目前登入 {status.account},且可以寫入 {status.repository}。",
            "如果 push 仍失敗,git 可能還沒接上 gh 的憑證。",
        ]
    if status.account_can_push:
        return [status.detail + "。"]
    others = [name for name in status.accounts if name != status.account]
    who = (
        f"資料庫綁定的帳號 {status.account}"
        if status.bound
        else f"gh 目前登入 {status.account}"
    )
    lines = [f"{who},但這個帳號對 {status.repository} 沒有寫入權。"]
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
            text=True,
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
