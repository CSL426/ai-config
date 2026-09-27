"""Can this machine push the data repository, and if not, why.

A machine can read the data repository but not write to it, which acg
reports as read-only. The cause is almost always one of three things:
no GitHub CLI, signed in as an account without write access, or a token
that git itself never sees. This module tells them apart so the CLI and
the desktop app can say which one it is, and offer the matching fix.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import ghauth_binding, ghauth_helper, ghauth_login

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


def git_push_probe(repo_dir: Path) -> "tuple[bool | None, str]":
    """Whether git itself can push right now, and git's own words if not.

    A dry-run push sends no objects and creates no ref. It is the only
    check that sees the whole picture: SSH keys, bound accounts and
    stored credentials alike. None means the failure was not an
    authentication refusal; the detail says what it was.
    """
    try:
        result = ghauth_binding._run_git(
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
    # 遠端已經回覆 ref 狀態才會拒絕 non-fast-forward:憑證與寫入權都通過了,
    # 只是本機落後,那是 pull 的事,不是登入的事
    if "[rejected]" in text and (
        "fetch first" in text or "non-fast-forward" in text
    ):
        return True, ""
    first = next((line for line in text.splitlines() if line.strip()), "git push 失敗")
    if any(marker in text.lower() for marker in ghauth_helper._AUTH_REFUSAL_MARKERS):
        return False, first
    return None, first


# git 上一次推送測試的錯誤文字;check_push_access 用它把原因說出來。
# 用模組變數而不是回傳值,是為了讓 git_can_push 維持可被測試替換的簡單簽名。
_last_push_detail = ""


def git_can_push(repo_dir: Path) -> "bool | None":
    global _last_push_detail
    verdict, _last_push_detail = git_push_probe(repo_dir)
    return verdict


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
    # 綁定的路徑可能還指著下載目錄那顆 exe;先改指目前的 acg 再讓 git 試
    ghauth_binding.refresh_binding(repo_dir)
    status.bound = ghauth_binding.bound_account(repo_dir)
    global _last_push_detail
    _last_push_detail = ""
    git_access = git_can_push(repo_dir) if repo_dir is not None else None
    git_detail = _last_push_detail if repo_dir is not None else ""
    status.can_push = git_access
    status.installed = shutil.which("gh") is not None
    active = ""
    if status.installed:
        try:
            active, status.accounts = ghauth_login._logged_in_accounts()
        except (OSError, subprocess.SubprocessError) as exc:
            status.detail = f"無法讀取 gh 登入狀態:{exc}"
    # 有綁定就只認綁定;沒綁定才退回 gh 的作用中帳號,而那是全機器共用的
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
        token = ghauth_helper.account_token(status.bound)
        if not token:
            if repo_dir is None:
                status.can_push = False
            status.detail = f"gh 沒有 {status.bound} 的登入紀錄,資料庫的綁定失效"
            return status
    try:
        result = ghauth_login._run_gh(
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
        # 說出 git 自己的錯誤,不然使用者只看到「無法確認」不知道要做什麼;
        # 綁定帳號時再親自跑一次 helper,分辨是 acg、gh 還是 git 設定的問題
        why = f":{git_detail}" if git_detail else ""
        probe = (
            f";{ghauth_helper.helper_self_test(status.bound, repo_dir)}" if status.bound else ""
        )
        status.detail = (
            f"{status.account} 有儲存庫寫入權,但 "
            + ("git 憑證驗證失敗" if git_access is False else "git 推送測試沒有成功")
            + why
            + probe
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
        # 綁定的帳號只屬於這個資料庫;沒綁定才是 gh 全機器共用的那個
        source = (
            f"這個資料庫綁定 {status.account}"
            if status.bound
            else f"這個資料庫沒有綁定帳號,目前用 gh 的作用中帳號 {status.account}"
        )
        return [
            f"{source},且可以寫入 {status.repository}。",
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
