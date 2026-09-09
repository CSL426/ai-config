"""Diagnosing why the data repository refuses a push."""

import io
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ai_config import ghauth
from ai_config.commands import login


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        # 多帳號常用的 SSH alias,URL 上看不出 host 其實是 github.com
        ("git@github-work:owner/repo.git", "owner/repo"),
        ("ssh://git@github-csl426:CSL426/myccskills.git", "CSL426/myccskills"),
        ("https://gitlab.com/owner/repo.git", ""),
        ("/srv/local/repo", ""),
        ("", ""),
    ],
)
def test_parse_github_repository(url: str, expected: str) -> None:
    assert ghauth.parse_github_repository(url) == expected


def test_non_github_remote_is_reported_as_unsupported() -> None:
    status = ghauth.check_push_access("https://gitlab.com/o/r.git")
    assert status.repository == ""
    assert status.actionable is False
    assert ghauth.describe(status) == [status.detail]


def test_missing_gh_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ghauth.shutil, "which", lambda name: None)

    status = ghauth.check_push_access("git@github.com:o/r.git")

    assert status.installed is False
    assert status.actionable is False
    assert any("cli.github.com" in line for line in ghauth.describe(status))


def _fake_gh(monkeypatch: pytest.MonkeyPatch, responses: dict) -> None:
    monkeypatch.setattr(ghauth.shutil, "which", lambda name: "/usr/bin/gh")

    def fake_run(args, **kwargs):
        key = " ".join(args[1:3])
        out, code = responses.get(key, ("", 1))
        return subprocess.CompletedProcess(args, code, stdout=out, stderr="")

    monkeypatch.setattr(ghauth.subprocess, "run", fake_run)


def test_account_without_write_access_lists_alternatives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_gh(
        monkeypatch,
        {
            "auth status": (
                (
                    "github.com\n"
                    "  ✓ Logged in to github.com account first\n"
                    "  - Active account: true\n"
                    "  ✓ Logged in to github.com account second\n"
                    "  - Active account: false\n"
                ),
                0,
            ),
            "api repos/o/r": ("false", 0),
        },
    )

    status = ghauth.check_push_access("git@github.com:o/r.git")

    assert status.account == "first"
    assert status.can_push is False
    assert status.actionable is True
    # 另一個已登入的帳號要講出來,那通常就是解法
    assert any("second" in line for line in ghauth.describe(status))


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

    status = ghauth.check_push_access("git@github.com:o/r.git")

    assert status.can_push is True
    assert status.actionable is False
    assert any("憑證" in line for line in ghauth.describe(status))


def test_repository_the_account_cannot_see_is_not_a_crash(
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
            "api repos/o/r": ("", 1),
        },
    )

    # 私有 repo 對這個帳號回 404,對使用者和「沒有權限」是同一件事
    status = ghauth.check_push_access("git@github.com:o/r.git")

    assert status.can_push is False
    assert status.actionable is True


def test_interactive_login_refuses_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghauth.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(ghauth.sys.stdin, "isatty", lambda: False)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not launch an interactive prompt")

    monkeypatch.setattr(ghauth.subprocess, "run", unexpected)

    # 沒有終端機時會永遠等不到輸入,那看起來就是當掉
    ok, detail = ghauth.run_interactive_login()

    assert ok is False
    assert "終端機" in detail


def test_client_id_is_acgs_own_oauth_app() -> None:
    # device flow 只用 client ID,不是機密;跟 gh 一樣寫死在原始碼,每個建置都能登入
    assert ghauth.GITHUB_CLIENT_ID.startswith("Ov23li")
    assert len(ghauth.GITHUB_CLIENT_ID) == 20
    assert ghauth.get_client_id({}) == ghauth.GITHUB_CLIENT_ID
    assert ghauth.device_login_available({}) is True


def test_get_client_id_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ghauth.get_client_id({"AI_CONFIG_GITHUB_CLIENT_ID": "abc"}) == "abc"


def test_get_client_id_explains_a_build_without_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghauth, "GITHUB_CLIENT_ID", "")
    with pytest.raises(ghauth.GhAuthError) as excinfo:
        ghauth.get_client_id({})
    assert "GITHUB_CLIENT_ID" in str(excinfo.value)
    assert ghauth.device_login_available({}) is False


def _fake_urlopen(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    import io
    import json as json_module

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json_module.dumps(payload).encode("utf-8")

    monkeypatch.setattr(ghauth.urllib.request, "urlopen", lambda *a, **k: Response())
    del io


def test_device_login_returns_a_code_for_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_urlopen(
        monkeypatch,
        {
            "device_code": "dev-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
            "expires_in": 900,
        },
    )

    flow = ghauth.start_device_login({"AI_CONFIG_GITHUB_CLIENT_ID": "abc"})

    assert flow["user_code"] == "ABCD-1234"
    assert flow["device_code"] == "dev-123"


def test_polling_reports_pending_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_urlopen(monkeypatch, {"error": "authorization_pending"})

    # 使用者還沒在瀏覽器上按確認,這不是錯誤
    assert (
        ghauth.poll_device_login("dev", 5, {"AI_CONFIG_GITHUB_CLIENT_ID": "a"}) is None
    )


def test_polling_returns_the_token_once_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_urlopen(monkeypatch, {"access_token": "gho_token"})

    token = ghauth.poll_device_login("dev", 5, {"AI_CONFIG_GITHUB_CLIENT_ID": "a"})

    assert token == "gho_token"


def test_polling_surfaces_a_real_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(
        monkeypatch,
        {"error": "expired_token", "error_description": "The code has expired"},
    )

    with pytest.raises(ghauth.GhAuthError) as excinfo:
        ghauth.poll_device_login("dev", 5, {"AI_CONFIG_GITHUB_CLIENT_ID": "a"})
    assert "expired" in str(excinfo.value).lower()


def test_store_token_reports_a_rejected_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghauth.shutil, "which", lambda name: "/usr/bin/gh")

    def fake_run(args, **kwargs):
        # store_token 先問一次 gh 的作用中帳號,再把 token 交給 gh 驗證
        if "--with-token" not in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        assert kwargs.get("input") == "bad-token"
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="error validating token: HTTP 401"
        )

    monkeypatch.setattr(ghauth.subprocess, "run", fake_run)

    ok, detail = ghauth.store_token("bad-token")

    # gh 會擋掉無效 token 且不動原本的登入,失敗要說清楚
    assert ok is False
    assert "401" in detail


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def test_binding_is_local_to_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "data"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0

    assert ghauth.bound_account(repo) == ""
    ok, detail = ghauth.bind_account(repo, "CSL426")
    assert ok, detail
    assert ghauth.bound_account(repo) == "CSL426"

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

    assert ghauth.unbind_account(repo) is True
    assert ghauth.bound_account(repo) == ""
    assert ghauth.unbind_account(repo) is False


def test_bind_rejects_unsafe_account_names(tmp_path: Path) -> None:
    ok, detail = ghauth.bind_account(tmp_path, "x; rm -rf /")
    assert ok is False and "不合法" in detail


def test_credential_helper_answers_get_with_the_bound_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(ghauth, "account_token", lambda account: f"tok-{account}")
    monkeypatch.setattr(
        ghauth.sys, "stdin", io.StringIO("protocol=https\nhost=github.com\n\n")
    )
    assert ghauth.credential_helper_main(["CSL426", "get"]) == 0
    assert capsys.readouterr().out == "username=CSL426\npassword=tok-CSL426\n"
    # store / erase 不做事,也不印
    assert ghauth.credential_helper_main(["CSL426", "erase"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(ghauth, "account_token", lambda account: "")
    monkeypatch.setattr(
        ghauth.sys, "stdin", io.StringIO("protocol=https\nhost=github.com\n\n")
    )
    assert ghauth.credential_helper_main(["CSL426", "get"]) == 1


def test_git_that_can_already_push_wins_over_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # SSH 金鑰本來就有寫入權的機器,不該因為 gh 登的是別的帳號被說成唯讀
    monkeypatch.setattr(ghauth, "git_can_push", lambda repo_dir: True)
    monkeypatch.setattr(ghauth, "bound_account", lambda repo_dir: "")
    monkeypatch.setattr(ghauth.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        ghauth, "_logged_in_accounts", lambda: ("first", ["first", "second"])
    )
    status = ghauth.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.accounts == ["first", "second"]
    assert status.can_push is True and status.actionable is False
    assert "git 已可推送" in ghauth.describe(status)[0]


def test_bound_account_is_judged_as_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ghauth, "git_can_push", lambda repo_dir: False)
    monkeypatch.setattr(ghauth, "bound_account", lambda repo_dir: "second")
    monkeypatch.setattr(ghauth, "account_token", lambda account: "tok-second")
    seen = {}

    def fake_run(args, **kwargs):
        key = " ".join(args[1:3])
        if key == "auth status":
            out = (
                "  ✓ Logged in to github.com account first\n  - Active account: true\n"
                "  ✓ Logged in to github.com account second\n  - Active account: false\n"
            )
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
        if key == "api repos/o/r":
            seen["token"] = (kwargs.get("env") or {}).get("GH_TOKEN")
            return subprocess.CompletedProcess(args, 0, stdout="true", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr(ghauth.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(ghauth.subprocess, "run", fake_run)
    status = ghauth.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.account == "second" and status.bound == "second"
    assert status.can_push is False
    assert status.account_can_push is True
    # 用綁定帳號的 token 問 GitHub,而不是 gh 的作用中帳號
    assert seen["token"] == "tok-second"


@pytest.mark.parametrize(
    "credential_request",
    [
        "protocol=https\nhost=untrusted.example\n\n",
        "protocol=http\nhost=github.com\n\n",
        "protocol=https\nhost=github.com.evil.example\n\n",
        "protocol=https\nhost=github.com:8443\n\n",
        "protocol=https\nhost=github.com\nhost=untrusted.example\n\n",
        "protocol=https\n\n",
        "",
    ],
)
def test_helper_refuses_untrusted_requests_without_reading_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    credential_request: str,
) -> None:
    monkeypatch.setattr(ghauth.sys, "stdin", io.StringIO(credential_request))

    def unexpected(account: str) -> str:
        pytest.fail("must not read a credential for an untrusted request")

    monkeypatch.setattr(ghauth, "account_token", unexpected)
    assert ghauth.credential_helper_main(["demo", "get"]) == 0
    assert capsys.readouterr().out == ""


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
    assert ghauth.bind_account(tmp_path, "first")[0]
    assert ghauth.bind_account(tmp_path, "second")[0]
    assert ghauth.bound_account(tmp_path) == "second"
    saved = _git(tmp_path, "config", "--get", ghauth._HELPERS_BACKUP).stdout
    assert json.loads(saved) == original
    assert ghauth.unbind_account(tmp_path)
    restored = _git(
        tmp_path, "config", "--local", "--null", "--get-all", "credential.helper"
    ).stdout
    assert restored[:-1].split("\0") == original
    assert (
        _git(tmp_path, "config", "--get", "example.preserved").stdout.strip()
        == "untouched"
    )
    assert _git(tmp_path, "config", "--get", ghauth._HELPERS_BACKUP).returncode == 1


@pytest.mark.parametrize("operation", ["bind", "rebind", "unbind"])
def test_binding_write_failure_preserves_config_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    _git(tmp_path, "config", "credential.helper", "cache")
    if operation != "bind":
        assert ghauth.bind_account(tmp_path, "first")[0]
    config = tmp_path / ".git" / "config"
    before = config.read_bytes()
    real_run = ghauth._run_git

    def fail_add(repo: Path, *args: str, **kwargs):
        if "--add" in args:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="injected failure"
            )
        return real_run(repo, *args, **kwargs)

    monkeypatch.setattr(ghauth, "_run_git", fail_add)
    if operation == "unbind":
        with pytest.raises(ghauth.GhAuthError, match="injected failure"):
            ghauth.unbind_account(tmp_path)
    else:
        ok, detail = ghauth.bind_account(tmp_path, "second")
        assert not ok and "injected failure" in detail
    assert config.read_bytes() == before
    assert not config.with_name("config.lock").exists()


def test_existing_git_config_lock_is_not_removed(tmp_path: Path) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    lock = tmp_path / ".git" / "config.lock"
    lock.write_text("another writer", encoding="utf-8")
    config = lock.with_name("config")
    before = config.read_bytes()
    assert not ghauth.bind_account(tmp_path, "demo")[0]
    assert config.read_bytes() == before
    assert lock.read_text(encoding="utf-8") == "another writer"


def test_helper_shell_quotes_executable_without_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = shutil.which("sh")
    if not shell:
        pytest.skip("requires the shell used by Git credential helpers")
    executable = "/tmp/fake $HOME $(printf injected) `printf injected` 'quoted'/python"
    monkeypatch.setattr(ghauth, "_acg_command", lambda: ["printf", "%s\\n", executable])
    result = subprocess.run(
        [shell, "-c", ghauth.helper_value("demo")[1:]],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HOME": "expanded"},
    )
    assert result.stdout.splitlines() == [executable, "__git-credential", "demo"]


def _mock_login_account(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(login, "SCRIPT_DIR", repo)
    monkeypatch.setattr(login, "_remote_url", lambda: "https://github.com/o/r.git")
    monkeypatch.setattr(ghauth.shutil, "which", lambda name: "/fake/gh")
    monkeypatch.setattr(
        ghauth, "_logged_in_accounts", lambda: ("first", ["first", "second"])
    )
    monkeypatch.setattr(ghauth, "account_token", lambda account: f"fake-{account}")
    monkeypatch.setattr(
        ghauth,
        "_run_gh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="true", stderr=""
        ),
    )


def test_login_can_rebind_when_git_already_pushes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    _mock_login_account(monkeypatch, tmp_path)
    monkeypatch.setattr(ghauth, "git_can_push", lambda repo: True)
    assert login.run_login("second") == 0
    assert ghauth.bound_account(tmp_path) == "second"


@pytest.mark.parametrize("after", [True, False, None])
def test_login_binds_existing_account_then_requires_git_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    after: "bool | None",
) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    _mock_login_account(monkeypatch, tmp_path)
    checks = iter([False, after])
    monkeypatch.setattr(ghauth, "git_can_push", lambda repo: next(checks))

    def unexpected_login():
        pytest.fail("the logged-in account can be bound without signing in again")

    monkeypatch.setattr(login, "run_interactive_login", unexpected_login)
    assert login.run_login() == (0 if after is True else 1)
    assert ghauth.bound_account(tmp_path) == "first"


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
    assert ghauth.bind_account(tmp_path, "demo")[0]
    assert ghauth.unbind_account(tmp_path)
    assert observed_modes == [0o600, 0o600]
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_malformed_helper_backup_leaves_binding_untouched(tmp_path: Path) -> None:
    assert _git(tmp_path, "init", "-q").returncode == 0
    assert ghauth.bind_account(tmp_path, "demo")[0]
    _git(tmp_path, "config", ghauth._HELPERS_BACKUP, '{"invalid": true}')
    config = tmp_path / ".git" / "config"
    before = config.read_bytes()
    assert not ghauth.bind_account(tmp_path, "second")[0]
    with pytest.raises(ghauth.GhAuthError):
        ghauth.unbind_account(tmp_path)
    assert config.read_bytes() == before


@pytest.mark.parametrize("login_succeeds", [True, False])
@pytest.mark.parametrize("restore_succeeds", [True, False])
def test_store_token_restores_account_even_after_login_failure(
    monkeypatch: pytest.MonkeyPatch,
    login_succeeds: bool,
    restore_succeeds: bool,
) -> None:
    monkeypatch.setattr(ghauth.shutil, "which", lambda name: "/fake/gh")
    accounts = iter(["first", "second"])
    monkeypatch.setattr(ghauth, "active_account", lambda: next(accounts))
    monkeypatch.setattr(
        ghauth.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [],
            0 if login_succeeds else 1,
            stdout="",
            stderr="",
        ),
    )
    restored = []

    def restore(account: str) -> tuple[bool, str]:
        restored.append(account)
        return restore_succeeds, "restore failed"

    monkeypatch.setattr(ghauth, "switch_account", restore)
    ok, detail = ghauth.store_token("fake-test-token")
    assert restored == ["first"]
    assert ok is (login_succeeds and restore_succeeds)
    if not restore_succeeds:
        assert "無法還原" in detail


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
        lambda *args: ghauth.GhStatus(
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


def test_ssh_push_success_does_not_claim_to_use_bound_https_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_login_account(monkeypatch, tmp_path)
    monkeypatch.setattr(ghauth, "bound_account", lambda repo: "second")
    monkeypatch.setattr(ghauth, "git_can_push", lambda repo: True)
    status = ghauth.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.can_push is True
    assert status.bound == "second"
    assert "使用綁定" not in status.detail
    assert "second" not in ghauth.describe(status)[0]


@pytest.mark.parametrize("api_returncode", [0, 1])
def test_api_denial_preserves_unknown_git_push_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    api_returncode: int,
) -> None:
    _mock_login_account(monkeypatch, tmp_path)
    monkeypatch.setattr(ghauth, "bound_account", lambda repo: "")
    monkeypatch.setattr(ghauth, "git_can_push", lambda repo: None)
    monkeypatch.setattr(
        ghauth,
        "_run_gh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [],
            api_returncode,
            stdout="false",
            stderr="",
        ),
    )
    status = ghauth.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.can_push is None
    assert status.account_can_push is (False if api_returncode == 0 else None)


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
    ok, detail = ghauth.bind_account(tmp_path, "second")
    assert not ok
    assert "HTTPS" in detail
    assert config.read_bytes() == before


def test_push_probe_reports_gits_own_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            128,
            stdout="",
            stderr="fatal: could not read Username for 'https://github.com': terminal prompts disabled\n",
        )

    monkeypatch.setattr(ghauth.subprocess, "run", fake_run)
    verdict, detail = ghauth.git_push_probe(tmp_path)
    assert verdict is None
    assert "could not read Username" in detail


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
            ghauth.helper_value("CSL426"),
        ).returncode
        == 0
    )

    ok, detail = ghauth.bind_account(repo, "CSL426")
    assert ok, detail
    helpers = _git(
        repo, "config", "--local", "--get-all", "credential.helper"
    ).stdout.splitlines()
    assert helpers == ["", ghauth.helper_value("CSL426")]

    ok, detail = ghauth.bind_account(repo, "other")
    assert ok, detail
    assert ghauth.bound_account(repo) == "other"
    assert ghauth.unbind_account(repo) is True
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
    ok, detail = ghauth.bind_account(repo, "CSL426")
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
        f"{result.stderr}\nhelper={ghauth.helper_value('CSL426')}"
    )
    assert "username=CSL426" in result.stdout
    assert "password=gho_fake_token" in result.stdout
