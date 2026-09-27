"""Binding the data repository to one GitHub account.

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
from pathlib import Path

from . import ghauth_login
from .paths import standalone_install_path
from .safety import is_reparse_point

HELPER_MARKER = "__git-credential"


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


def helper_executable() -> "tuple[Path, Path | None]":
    """The executable git should call, and the running copy if it differs.

    A standalone exe is often run once from the download folder before
    the installer copies it to ~/.local/bin. The binding must outlive
    that copy, so it points at the installed executable whenever one
    exists; the second value names the running copy when it was not used.
    """
    running = Path(sys.executable)
    if not getattr(sys, "frozen", False):
        return running, None
    installed = standalone_install_path()
    try:
        if installed.is_file():
            # 同一個檔案時也要回傳固定入口:running 是解析過的
            # versions/<版號>/ 路徑,那個目錄更新幾次後就被清掉
            return installed, None if installed.samefile(running) else running
    except OSError:
        pass
    return running, None


def _acg_command() -> list[str]:
    executable = str(helper_executable()[0])
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


def bound_helper(repo_dir: "Path | None") -> str:
    """The credential.helper line acg wrote, or "" when not bound."""
    if repo_dir is None:
        return ""
    result = _run_git(repo_dir, "config", "--local", "--get-all", "credential.helper")
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if _BOUND.search(line):
            return line
    return ""


def bound_account(repo_dir: "Path | None") -> str:
    match = _BOUND.search(bound_helper(repo_dir))
    return match[1] if match else ""


def refresh_binding(repo_dir: "Path | None") -> bool:
    """Re-point a binding that names a different acg than the one to use.

    The helper line carries an absolute path; the exe the user bound
    from may since have been installed, updated or removed. Any check
    that is about to run git first makes the line current, so git tests
    the same acg the user is running. Returns whether it changed.
    """
    current = bound_helper(repo_dir)
    account = bound_account(repo_dir)
    if not account or current == helper_value(account):
        return False
    try:
        return _change_binding(repo_dir, account)
    except (OSError, subprocess.SubprocessError, ghauth_login.GhAuthError, ValueError):
        return False


_HELPERS_BACKUP = "acg.previousCredentialHelpers"


def _change_binding(repo_dir: Path, account: "str | None") -> bool:
    """Publish helper changes together, using the same lock as git config."""
    location = _run_git(repo_dir, "rev-parse", "--git-path", "config")
    if location.returncode:
        raise ghauth_login.GhAuthError((location.stderr or location.stdout).strip())
    config = Path(location.stdout.strip())
    if not config.is_absolute():
        config = repo_dir / config
    if is_reparse_point(config) or not config.is_file():
        raise ghauth_login.GhAuthError("git config 不是一般檔案,無法安全修改綁定")
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
                raise ghauth_login.GhAuthError(
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
                raise ghauth_login.GhAuthError("憑證備份格式不正確,保留目前設定")
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
    except (OSError, subprocess.SubprocessError, ghauth_login.GhAuthError, ValueError) as exc:
        return False, str(exc)
    bound, running = helper_executable()
    if running is not None:
        return True, f"綁定指向安裝位置 {bound},不是目前執行的 {running}"
    return True, ""


def unbind_account(repo_dir: Path) -> bool:
    """Restore the original local helpers; failures leave config untouched."""
    try:
        return _change_binding(repo_dir, None)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ghauth_login.GhAuthError(f"無法解除綁定:{exc}") from exc
