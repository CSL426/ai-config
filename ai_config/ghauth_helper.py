"""The credential helper Git calls: hand over the bound account's token."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import ghauth_binding, ghauth_login


def account_token(account: str) -> str:
    try:
        result = ghauth_login._run_gh("auth", "token", "--hostname", "github.com", "--user", account)
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
        sys.stderr.write("acg credential helper: 沒有收到 git 的請求(stdin 是終端機)\n")
        return 0
    request: dict[str, str] = {}
    for line in sys.stdin:
        line = line.rstrip("\r\n")
        if not line:
            break
        key, separator, value = line.partition("=")
        # git 2.46 起推送時會送多行 capability[]、wwwauth[];`[]` 結尾的鍵本來就
        # 允許重複,其他鍵重複才是壞掉的請求
        if not separator or (key in request and not key.endswith("[]")):
            return 0
        request[key] = value
    # A repo-local helper can also be asked about other remotes in that repo.
    if request.get("protocol") != "https" or request.get("host") != "github.com":
        return 0
    if not re.fullmatch(r"[A-Za-z0-9-]+", account):
        return 1
    token = account_token(account)
    if not token:
        # git 會把 helper 的 stderr 原樣轉給使用者;說清楚是哪一步沒拿到
        located = shutil.which("gh") or "(PATH 裡找不到 gh)"
        sys.stderr.write(
            f"acg credential helper: gh auth token --user {account} 沒有回傳 token"
            f"(gh={located});請先在這台 gh auth login 登入 {account}\n"
        )
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


def helper_self_test(account: str, repo_dir: "Path | None" = None) -> str:
    """Run the bound helper the way git would and describe the outcome.

    When git says 'could not read Username', this is the step that tells
    whether acg itself failed to start, gh had no token, or the helper
    answered fine and the problem lies elsewhere. With ``repo_dir`` the
    request goes through ``git credential fill`` in that repository, so
    it exercises the very command line git has in its config rather
    than the copy of acg that happens to be running.
    """
    # 照 git 2.46+ 推送時的請求形狀餵,含重複的 capability[] 行
    request = (
        "capability[]=authtype\ncapability[]=state\n"
        "protocol=https\nhost=github.com\n"
        'wwwauth[]=Basic realm="GitHub"\n\n'
    )
    if repo_dir is not None:
        command = ["git", "-C", str(repo_dir), "credential", "fill"]
    else:
        command = [*ghauth_binding._acg_command(), ghauth_binding.HELPER_MARKER, account, "get"]
    try:
        result = subprocess.run(
            command,
            input=request,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"helper 無法啟動:{exc}"
    if result.returncode == 0 and "password=" in result.stdout:
        if repo_dir is not None:
            return "git 經設定的 helper 取得憑證正常,問題出在推送本身的設定"
        return "helper 直接執行正常,問題出在 git 呼叫 helper 的設定"
    first = next(
        (
            line
            for line in (result.stderr or result.stdout).splitlines()
            if line.strip()
        ),
        f"exit {result.returncode}",
    )
    return f"helper 失敗:{first}"
