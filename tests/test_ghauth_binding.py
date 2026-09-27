"""Binding the data repository to one GitHub account through a credential helper."""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from ghauth_test_helpers import (
    _fake_gh,
    _git,
    _mock_login_account,
)

from ai_config import (
    ghauth_access,
    ghauth_binding,
    ghauth_login,
    hooks,
)
from ai_config.commands import login

# conftest 會在每個測試前換掉它;先在 import 時留住真正的實作
REAL_BINDING_REFRESH = hooks._refresh_credential_binding


def test_write_access_points_at_the_credential_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_gh(
        monkeypatch,
        {
            "auth status": (
                (
                    "  ✓ Logged in to github.com account owner\n"
                    "  - Active account: true\n"
                ),
                0,
            ),
            "api repos/o/r": ("true", 0),
        },
    )

    status = ghauth_access.check_push_access("git@github.com:o/r.git")

    assert status.can_push is True
    assert status.actionable is False
    assert any("憑證" in line for line in ghauth_access.describe(status))


def test_binding_is_local_to_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "data"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0

    assert ghauth_binding.bound_account(repo) == ""
    ok, detail = ghauth_binding.bind_account(repo, "CSL426")
    assert ok, detail
    assert ghauth_binding.bound_account(repo) == "CSL426"

    helpers = _git(
        repo, "config", "--local", "--get-all", "credential.helper"
    ).stdout.splitlines()
    # 第一項是空字串,用來清掉全域的 helper;第二項才是 acg 自己
    assert helpers[0] == ""
    assert helpers[1].startswith("!") and helpers[1].endswith("__git-credential CSL426")
    # 只在 repo 本地,全域設定不動
    assert "__git-credential" not in (
        subprocess.run(
            ["git", "config", "--global", "--get-all", "credential.helper"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    )

    assert ghauth_binding.unbind_account(repo) is True
    assert ghauth_binding.bound_account(repo) == ""
    assert ghauth_binding.unbind_account(repo) is False


def test_bind_rejects_unsafe_account_names(tmp_path: Path) -> None:
    ok, detail = ghauth_binding.bind_account(tmp_path, "x; rm -rf /")
    assert ok is False and "不合法" in detail


def test_rebinding_preserves_original_helpers_and_other_config(
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    original = ["", "cache --timeout=999", '!printf "line one\\nline two"']
    for helper in original:
        assert (
            _git(tmp_path, "config", "--add", "credential.helper", helper).returncode
            == 0
        )
    _git(tmp_path, "config", "example.preserved", "untouched")
    assert ghauth_binding.bind_account(tmp_path, "first")[0]
    assert ghauth_binding.bind_account(tmp_path, "second")[0]
    assert ghauth_binding.bound_account(tmp_path) == "second"
    saved = _git(tmp_path, "config", "--get", ghauth_binding._HELPERS_BACKUP).stdout
    assert json.loads(saved) == original
    assert ghauth_binding.unbind_account(tmp_path)
    restored = _git(
        tmp_path, "config", "--local", "--null", "--get-all", "credential.helper"
    ).stdout
    assert restored[:-1].split("\0") == original
    assert (
        _git(tmp_path, "config", "--get", "example.preserved").stdout.strip()
        == "untouched"
    )
    assert _git(tmp_path, "config", "--get", ghauth_binding._HELPERS_BACKUP).returncode == 1


@pytest.mark.parametrize("operation", ["bind", "rebind", "unbind"])
def test_binding_write_failure_preserves_config_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    _git(tmp_path, "config", "credential.helper", "cache")
    if operation != "bind":
        assert ghauth_binding.bind_account(tmp_path, "first")[0]
    config = tmp_path / ".git" / "config"
    before = config.read_bytes()
    real_run = ghauth_binding._run_git

    def fail_add(repo: Path, *args: str, **kwargs):
        if "--add" in args:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="injected failure"
            )
        return real_run(repo, *args, **kwargs)

    monkeypatch.setattr(ghauth_binding, "_run_git", fail_add)
    if operation == "unbind":
        with pytest.raises(ghauth_login.GhAuthError, match="injected failure"):
            ghauth_binding.unbind_account(tmp_path)
    else:
        ok, detail = ghauth_binding.bind_account(tmp_path, "second")
        assert not ok and "injected failure" in detail
    assert config.read_bytes() == before
    assert not config.with_name("config.lock").exists()


def test_existing_git_config_lock_is_not_removed(tmp_path: Path) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    lock = tmp_path / ".git" / "config.lock"
    lock.write_text("another writer", encoding="utf-8")
    config = lock.with_name("config")
    before = config.read_bytes()
    assert not ghauth_binding.bind_account(tmp_path, "demo")[0]
    assert config.read_bytes() == before
    assert lock.read_text(encoding="utf-8") == "another writer"


def test_helper_shell_quotes_executable_without_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = shutil.which("sh")
    if not shell:
        pytest.skip("requires the shell used by Git credential helpers")
    executable = "/tmp/fake $HOME $(printf injected) `printf injected` 'quoted'/python"
    monkeypatch.setattr(ghauth_binding, "_acg_command", lambda: ["printf", "%s\\n", executable])
    result = subprocess.run(
        [shell, "-c", ghauth_binding.helper_value("demo")[1:]],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HOME": "expanded"},
    )
    assert result.stdout.splitlines() == [executable, "__git-credential", "demo"]


def test_login_can_rebind_when_git_already_pushes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    _mock_login_account(monkeypatch, tmp_path)
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo: True)
    assert login.run_login("second") == 0
    assert ghauth_binding.bound_account(tmp_path) == "second"


@pytest.mark.parametrize("after", [True, False, None])
def test_login_binds_existing_account_then_requires_git_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    after: "bool | None",
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    _mock_login_account(monkeypatch, tmp_path)
    checks = iter([False, after])
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo: next(checks))

    def unexpected_login():
        pytest.fail("the logged-in account can be bound without signing in again")

    monkeypatch.setattr(login, "run_interactive_login", unexpected_login)
    assert login.run_login() == (0 if after is True else 1)
    assert ghauth_binding.bound_account(tmp_path) == "first"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_binding_lock_is_private_before_copying_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    config = tmp_path / ".git" / "config"
    config.chmod(0o600)
    original_read = Path.read_bytes
    observed_modes = []

    def inspect_mode(path: Path) -> bytes:
        if path == config:
            observed_modes.append(
                stat.S_IMODE(config.with_name("config.lock").stat().st_mode)
            )
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", inspect_mode)
    assert ghauth_binding.bind_account(tmp_path, "demo")[0]
    assert ghauth_binding.unbind_account(tmp_path)
    assert observed_modes == [0o600, 0o600]
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_malformed_helper_backup_leaves_binding_untouched(tmp_path: Path) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    assert ghauth_binding.bind_account(tmp_path, "demo")[0]
    _git(tmp_path, "config", ghauth_binding._HELPERS_BACKUP, '{"invalid": true}')
    config = tmp_path / ".git" / "config"
    before = config.read_bytes()
    assert not ghauth_binding.bind_account(tmp_path, "second")[0]
    with pytest.raises(ghauth_login.GhAuthError):
        ghauth_binding.unbind_account(tmp_path)
    assert config.read_bytes() == before


@pytest.mark.parametrize("login_succeeds", [True, False])
def test_login_does_not_bind_when_previous_account_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    login_succeeds: bool,
) -> None:
    _mock_login_account(monkeypatch, tmp_path)
    monkeypatch.setattr(
        login,
        "check_push_access",
        lambda *args: ghauth_access.GhStatus(
            installed=True,
            logged_in=True,
            account="first",
            repository="o/r",
            can_push=False,
            accounts=["first"],
        ),
    )
    accounts = iter(["first", "second"])
    monkeypatch.setattr(login, "active_account", lambda: next(accounts))
    monkeypatch.setattr(login, "run_interactive_login", lambda: (login_succeeds, ""))
    restored = []

    def restore(account: str) -> tuple[bool, str]:
        restored.append(account)
        return False, "restore failed"

    monkeypatch.setattr(login, "switch_account", restore)

    def unexpected_binding(*args):
        pytest.fail("must not bind after failing to restore the global account")

    monkeypatch.setattr(login, "bind_account", unexpected_binding)
    assert login.run_login() == 1
    assert restored == ["first"]


@pytest.mark.parametrize(
    "push_url",
    [
        "git@github.com:o/r.git",
        "https://untrusted.example/o/r.git",
    ],
)
def test_binding_rejects_push_urls_that_bypass_the_https_helper(
    tmp_path: Path,
    push_url: str,
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    _git(tmp_path, "remote", "add", "origin", "https://github.com/o/r.git")
    _git(tmp_path, "config", "remote.origin.pushurl", push_url)
    config = tmp_path / ".git" / "config"
    before = config.read_bytes()
    ok, detail = ghauth_binding.bind_account(tmp_path, "second")
    assert not ok
    assert "HTTPS" in detail
    assert config.read_bytes() == before


def test_binding_twice_keeps_one_helper_and_survives_a_clone_time_binding(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "data"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    # 模擬 clone -c 直接寫入的綁定:沒有備份記錄
    assert (
        _git(repo, "config", "--local", "--add", "credential.helper", "").returncode
        == 0
    )
    assert (
        _git(
            repo,
            "config",
            "--local",
            "--add",
            "credential.helper",
            ghauth_binding.helper_value("CSL426"),
        ).returncode
        == 0
    )

    ok, detail = ghauth_binding.bind_account(repo, "CSL426")
    assert ok, detail
    helpers = _git(
        repo, "config", "--local", "--get-all", "credential.helper"
    ).stdout.splitlines()
    assert helpers == ["", ghauth_binding.helper_value("CSL426")]

    ok, detail = ghauth_binding.bind_account(repo, "other")
    assert ok, detail
    assert ghauth_binding.bound_account(repo) == "other"
    assert ghauth_binding.unbind_account(repo) is True
    assert (
        _git(repo, "config", "--local", "--get-all", "credential.helper").stdout == ""
    )


def test_git_obtains_credentials_through_the_bound_helper(
    tmp_path: Path, monkeypatch
) -> None:
    """git → sh → acg __git-credential → gh; the whole chain, on every OS the CI runs."""
    import os
    import sys

    repo = tmp_path / "data"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    ok, detail = ghauth_binding.bind_account(repo, "CSL426")
    assert ok, detail

    # 假的 gh:只回應 auth token --user
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    if os.name == "nt":
        (fake_bin / "gh.cmd").write_text(
            "@echo off\r\necho gho_fake_token\r\n", encoding="utf-8"
        )
    else:
        script = fake_bin / "gh"
        script.write_text("#!/bin/sh\necho gho_fake_token\n", encoding="utf-8")
        script.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["GIT_TERMINAL_PROMPT"] = "0"

    result = subprocess.run(
        ["git", "-C", str(repo), "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    helper_probe = subprocess.run(
        [sys.executable, "-m", "ai_config", "__git-credential", "CSL426", "get"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert helper_probe.returncode == 0, helper_probe.stderr
    assert "password=gho_fake_token" in helper_probe.stdout
    assert result.returncode == 0, (
        f"{result.stderr}\nhelper={ghauth_binding.helper_value('CSL426')}"
    )
    assert "username=CSL426" in result.stdout
    assert "password=gho_fake_token" in result.stdout


def test_binding_prefers_the_installed_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config import paths

    downloaded = tmp_path / "Downloads" / "acg"
    installed = tmp_path / ".local" / "bin" / "ai-config"
    downloaded.parent.mkdir()
    downloaded.write_text("")
    monkeypatch.setattr(ghauth_binding.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ghauth_binding.sys, "executable", str(downloaded))
    monkeypatch.setattr(ghauth_binding, "standalone_install_path", lambda: installed)
    monkeypatch.setattr(paths, "standalone_install_path", lambda: installed)

    # 還沒安裝:只能指向手上這一份
    assert ghauth_binding.helper_executable() == (downloaded, None)

    installed.parent.mkdir(parents=True)
    installed.write_text("")
    assert ghauth_binding.helper_executable() == (installed, downloaded)
    # Windows 上會轉成正斜線給 git 的 sh 用;Linux 的 as_posix 就是原字串
    assert ghauth_binding._acg_command() == [installed.as_posix()]


def test_stale_binding_is_repointed_before_git_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "data"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    monkeypatch.setattr(ghauth_binding, "_acg_command", lambda: ["/downloads/acg"])
    ok, detail = ghauth_binding.bind_account(repo, "CSL426")
    assert ok, detail
    stale = ghauth_binding.helper_value("CSL426")
    assert ghauth_binding.refresh_binding(repo) is False

    # exe 之後裝到安裝位置:綁定要跟著改指,備份的原始 helper 清單不動
    monkeypatch.setattr(ghauth_binding, "_acg_command", lambda: ["/installed/ai-config"])
    assert ghauth_binding.refresh_binding(repo) is True
    helpers = _git(
        repo, "config", "--local", "--get-all", "credential.helper"
    ).stdout.splitlines()
    assert helpers == ["", ghauth_binding.helper_value("CSL426")]
    assert stale not in helpers
    assert _git(repo, "config", "--local", ghauth_binding._HELPERS_BACKUP).stdout.strip() == "[]"
    assert ghauth_binding.refresh_binding(repo) is False
    assert ghauth_binding.unbind_account(repo) is True
    assert (
        _git(repo, "config", "--local", "--get-all", "credential.helper").stdout == ""
    )


def test_the_helper_names_the_launcher_not_the_version_it_resolves_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 從 ~/.local/bin 啟動時 sys.executable 是解析後的 versions/<版號>/ 路徑;
    # 綁定寫那個,更新幾次後目錄被清掉,push 就先失敗一次才被修回來
    version_dir = tmp_path / "versions" / "1.0.91"
    version_dir.mkdir(parents=True)
    running = version_dir / "ai-config"
    running.write_text("")
    installed = tmp_path / "bin" / "ai-config"
    installed.parent.mkdir()
    try:
        installed.symlink_to(running)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(ghauth_binding.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ghauth_binding.sys, "executable", str(running))
    monkeypatch.setattr(ghauth_binding, "standalone_install_path", lambda: installed)

    assert ghauth_binding.helper_executable() == (installed, None)


def test_apply_and_update_repoint_the_credential_binding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config import paths

    # conftest 在測試裡關掉這一步;這裡要測的就是它
    monkeypatch.setattr(hooks, "_refresh_credential_binding", REAL_BINDING_REFRESH)
    seen = []
    monkeypatch.setattr(hooks, "refresh_detail", dict)
    monkeypatch.setattr(ghauth_binding, "refresh_binding",
                        lambda repo: seen.append(repo) or True)

    hooks.refresh_all()

    assert seen == [paths.SCRIPT_DIR]
    assert "credential helper" in capsys.readouterr().out
