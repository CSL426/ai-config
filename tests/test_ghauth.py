"""Diagnosing why the data repository refuses a push."""

import subprocess

import pytest

from ai_config import ghauth


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


def test_client_id_constant_is_empty_in_source() -> None:
    # 和 Google Drive 一樣:公開儲存庫裡不放 client ID,由建置注入
    assert ghauth.GITHUB_CLIENT_ID == ""


def test_get_client_id_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ghauth.get_client_id({"AI_CONFIG_GITHUB_CLIENT_ID": "abc"}) == "abc"


def test_get_client_id_explains_a_build_without_login() -> None:
    with pytest.raises(ghauth.GhAuthError) as excinfo:
        ghauth.get_client_id({})
    assert "GITHUB_CLIENT_ID" in str(excinfo.value)


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

    monkeypatch.setattr(
        ghauth.urllib.request, "urlopen", lambda *a, **k: Response()
    )
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
    assert ghauth.poll_device_login("dev", 5, {"AI_CONFIG_GITHUB_CLIENT_ID": "a"}) is None


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
        assert kwargs.get("input") == "bad-token"
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="error validating token: HTTP 401"
        )

    monkeypatch.setattr(ghauth.subprocess, "run", fake_run)

    ok, detail = ghauth.store_token("bad-token")

    # gh 會擋掉無效 token 且不動原本的登入,失敗要說清楚
    assert ok is False
    assert "401" in detail
