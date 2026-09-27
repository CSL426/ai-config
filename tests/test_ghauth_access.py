"""GitHub access: can this machine push, why not, and signing in to fix it."""

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
    ghauth_helper,
    ghauth_login,
)


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
    assert ghauth_access.parse_github_repository(url) == expected


def test_non_github_remote_is_reported_as_unsupported() -> None:
    status = ghauth_access.check_push_access("https://gitlab.com/o/r.git")
    assert status.repository == ""
    assert status.actionable is False
    assert ghauth_access.describe(status) == [status.detail]


def test_missing_gh_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: None)

    status = ghauth_access.check_push_access("git@github.com:o/r.git")

    assert status.installed is False
    assert status.actionable is False
    assert any("cli.github.com" in line for line in ghauth_access.describe(status))


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

    status = ghauth_access.check_push_access("git@github.com:o/r.git")

    assert status.account == "first"
    assert status.can_push is False
    assert status.actionable is True
    # 另一個已登入的帳號要講出來,那通常就是解法
    assert any("second" in line for line in ghauth_access.describe(status))


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
    status = ghauth_access.check_push_access("git@github.com:o/r.git")

    assert status.can_push is False
    assert status.actionable is True


def test_interactive_login_refuses_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(ghauth_binding.sys.stdin, "isatty", lambda: False)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not launch an interactive prompt")

    monkeypatch.setattr(ghauth_access.subprocess, "run", unexpected)

    # 沒有終端機時會永遠等不到輸入,那看起來就是當掉
    ok, detail = ghauth_login.run_interactive_login()

    assert ok is False
    assert "終端機" in detail


def test_client_id_is_acgs_own_oauth_app() -> None:
    # device flow 只用 client ID,不是機密;跟 gh 一樣寫死在原始碼,每個建置都能登入
    assert ghauth_login.GITHUB_CLIENT_ID.startswith("Ov23li")
    assert len(ghauth_login.GITHUB_CLIENT_ID) == 20
    assert ghauth_login.get_client_id({}) == ghauth_login.GITHUB_CLIENT_ID
    assert ghauth_login.device_login_available({}) is True


def test_get_client_id_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ghauth_login.get_client_id({"AI_CONFIG_GITHUB_CLIENT_ID": "abc"}) == "abc"


def test_get_client_id_explains_a_build_without_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghauth_login, "GITHUB_CLIENT_ID", "")
    with pytest.raises(ghauth_login.GhAuthError) as excinfo:
        ghauth_login.get_client_id({})
    assert "GITHUB_CLIENT_ID" in str(excinfo.value)
    assert ghauth_login.device_login_available({}) is False


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

    monkeypatch.setattr(ghauth_login.urllib.request, "urlopen", lambda *a, **k: Response())
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

    flow = ghauth_login.start_device_login({"AI_CONFIG_GITHUB_CLIENT_ID": "abc"})

    assert flow["user_code"] == "ABCD-1234"
    assert flow["device_code"] == "dev-123"


def test_polling_reports_pending_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_urlopen(monkeypatch, {"error": "authorization_pending"})

    # 使用者還沒在瀏覽器上按確認,這不是錯誤
    assert (
        ghauth_login.poll_device_login("dev", 5, {"AI_CONFIG_GITHUB_CLIENT_ID": "a"}) is None
    )


def test_polling_returns_the_token_once_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_urlopen(monkeypatch, {"access_token": "gho_token"})

    token = ghauth_login.poll_device_login("dev", 5, {"AI_CONFIG_GITHUB_CLIENT_ID": "a"})

    assert token == "gho_token"


def test_polling_surfaces_a_real_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_urlopen(
        monkeypatch,
        {"error": "expired_token", "error_description": "The code has expired"},
    )

    with pytest.raises(ghauth_login.GhAuthError) as excinfo:
        ghauth_login.poll_device_login("dev", 5, {"AI_CONFIG_GITHUB_CLIENT_ID": "a"})
    assert "expired" in str(excinfo.value).lower()


def test_store_token_reports_a_rejected_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/usr/bin/gh")

    def fake_run(args, **kwargs):
        # store_token 先問一次 gh 的作用中帳號,再把 token 交給 gh 驗證
        if "--with-token" not in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        assert kwargs.get("input") == "bad-token"
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="error validating token: HTTP 401"
        )

    monkeypatch.setattr(ghauth_access.subprocess, "run", fake_run)

    ok, detail = ghauth_login.store_token("bad-token")

    # gh 會擋掉無效 token 且不動原本的登入,失敗要說清楚
    assert ok is False
    assert "401" in detail


def test_git_that_can_already_push_wins_over_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # SSH 金鑰本來就有寫入權的機器,不該因為 gh 登的是別的帳號被說成唯讀
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo_dir: True)
    monkeypatch.setattr(ghauth_binding, "bound_account", lambda repo_dir: "")
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        ghauth_login, "_logged_in_accounts", lambda: ("first", ["first", "second"])
    )
    status = ghauth_access.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.accounts == ["first", "second"]
    assert status.can_push is True and status.actionable is False
    assert "git 已可推送" in ghauth_access.describe(status)[0]


def test_bound_account_is_judged_as_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo_dir: False)
    monkeypatch.setattr(ghauth_binding, "bound_account", lambda repo_dir: "second")
    monkeypatch.setattr(ghauth_helper, "account_token", lambda account: "tok-second")
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

    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(ghauth_access.subprocess, "run", fake_run)
    status = ghauth_access.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.account == "second" and status.bound == "second"
    assert status.can_push is False
    assert status.account_can_push is True
    # 用綁定帳號的 token 問 GitHub,而不是 gh 的作用中帳號
    assert seen["token"] == "tok-second"


@pytest.mark.parametrize("login_succeeds", [True, False])
@pytest.mark.parametrize("restore_succeeds", [True, False])
def test_store_token_restores_account_even_after_login_failure(
    monkeypatch: pytest.MonkeyPatch,
    login_succeeds: bool,
    restore_succeeds: bool,
) -> None:
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/fake/gh")
    accounts = iter(["first", "second"])
    monkeypatch.setattr(ghauth_login, "active_account", lambda: next(accounts))
    monkeypatch.setattr(
        ghauth_access.subprocess,
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

    monkeypatch.setattr(ghauth_login, "switch_account", restore)
    ok, detail = ghauth_login.store_token("fake-test-token")
    assert restored == ["first"]
    assert ok is (login_succeeds and restore_succeeds)
    if not restore_succeeds:
        assert "無法還原" in detail


def test_ssh_push_success_does_not_claim_to_use_bound_https_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_login_account(monkeypatch, tmp_path)
    monkeypatch.setattr(ghauth_binding, "bound_account", lambda repo: "second")
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo: True)
    status = ghauth_access.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.can_push is True
    assert status.bound == "second"
    assert "使用綁定" not in status.detail
    assert "second" not in ghauth_access.describe(status)[0]


@pytest.mark.parametrize("api_returncode", [0, 1])
def test_api_denial_preserves_unknown_git_push_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    api_returncode: int,
) -> None:
    _mock_login_account(monkeypatch, tmp_path)
    monkeypatch.setattr(ghauth_binding, "bound_account", lambda repo: "")
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo: None)
    monkeypatch.setattr(
        ghauth_login,
        "_run_gh",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [],
            api_returncode,
            stdout="false",
            stderr="",
        ),
    )
    status = ghauth_access.check_push_access("git@github.com:o/r.git", tmp_path)
    assert status.can_push is None
    assert status.account_can_push is (False if api_returncode == 0 else None)


def test_push_probe_treats_a_stale_local_branch_as_authenticated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 遠端能回「fetch first」代表憑證與寫入權都過了,只是本機落後
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=(
                "To https://github.com/o/r\n"
                " ! [rejected]        HEAD -> main (fetch first)\n"
                "error: failed to push some refs to 'https://github.com/o/r'\n"
            ),
        )

    monkeypatch.setattr(ghauth_access.subprocess, "run", fake_run)
    assert ghauth_access.git_push_probe(tmp_path) == (True, "")


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

    monkeypatch.setattr(ghauth_access.subprocess, "run", fake_run)
    verdict, detail = ghauth_access.git_push_probe(tmp_path)
    assert verdict is None
    assert "could not read Username" in detail


def test_a_bound_repository_reports_its_own_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 綁定的帳號只屬於這個資料庫,不該退回 gh 全機器的作用中帳號
    repo = tmp_path / "data"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    monkeypatch.setattr(ghauth_binding, "_acg_command", lambda: ["/x/acg"])
    assert ghauth_binding.bind_account(repo, "bound-one")[0]

    # 只替換 gh 與 git 的查詢,不動 subprocess 本身,否則讀不到綁定
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        ghauth_login, "_logged_in_accounts", lambda: ("machine-wide", ["machine-wide"])
    )
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo_dir: True)

    status = ghauth_access.check_push_access("https://github.com/o/r.git", repo)

    assert status.bound == "bound-one"
    assert status.account == "bound-one"
    assert all("machine-wide" not in line for line in ghauth_access.describe(status))


def test_an_unbound_repository_follows_the_machine_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "data"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    monkeypatch.setattr(ghauth_login.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        ghauth_login, "_logged_in_accounts", lambda: ("machine-wide", ["machine-wide"])
    )
    monkeypatch.setattr(ghauth_access, "git_can_push", lambda repo_dir: False)
    monkeypatch.setattr(ghauth_helper, "account_token", lambda account: "tok")
    monkeypatch.setattr(
        ghauth_login,
        "_run_gh",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="true", stderr=""),
    )

    status = ghauth_access.check_push_access("https://github.com/o/r.git", repo)

    assert status.bound == ""
    assert status.account == "machine-wide"
