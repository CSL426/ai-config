"""Git repositories and fake gh calls shared by the ghauth tests."""

import subprocess
from pathlib import Path

import pytest

from ai_config import (
    ghauth_access,
    ghauth_helper,
    ghauth_login,
)
from ai_config.commands import login


def _fake_gh(monkeypatch: pytest.MonkeyPatch, responses: dict) -> None:
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/usr/bin/gh")

    def fake_run(args, **kwargs):
        key = " ".join(args[1:3])
        out, code = responses.get(key, ("", 1))
        return subprocess.CompletedProcess(args, code, stdout=out, stderr="")

    monkeypatch.setattr(ghauth_access.subprocess, "run", fake_run)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _mock_login_account(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(login, "SCRIPT_DIR", repo)
    monkeypatch.setattr(login, "_remote_url", lambda: "https://github.com/o/r.git")
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/fake/gh")
    monkeypatch.setattr(
        ghauth_login, "_logged_in_accounts", lambda: ("first", ["first", "second"])
    )
    monkeypatch.setattr(ghauth_helper, "account_token", lambda account: f"fake-{account}")
    monkeypatch.setattr(
        ghauth_login,
        "_run_gh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="true", stderr=""
        ),
    )
    # 自我檢查會真的跑 git credential fill,碰到開發機的全域 helper;這裡不測它
    monkeypatch.setattr(
        ghauth_helper, "helper_self_test", lambda account, repo_dir=None: "helper 略過"
    )
