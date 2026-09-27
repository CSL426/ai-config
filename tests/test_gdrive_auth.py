"""Google Drive sign-in: client ID and secret, PKCE, tokens and their scope."""

import os
import urllib.error
from pathlib import Path
from typing import Any, Self

import pytest
from gdrive_test_helpers import _isolated_config  # noqa: F401

from ai_config.config import (
    save_data_repo,
)
from ai_config.gdrive_auth import (
    GDRIVE_CLIENT_ID,
    GDRIVE_CLIENT_SECRET,
    GDRIVE_SCOPE,
    GDriveAuthError,
    delete_token,
    generate_pkce,
    get_client_id,
    get_valid_access_token,
    load_token,
    save_token,
)
from ai_config.paths import EXCLUDED_FILES


def test_token_file_in_excluded_files() -> None:
    assert "gdrive_token.json" in EXCLUDED_FILES


def test_client_secret_constant_is_empty_in_source() -> None:
    assert GDRIVE_CLIENT_SECRET == ""


def test_refresh_includes_client_secret_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.gdrive_auth import refresh_access_token

    monkeypatch.setenv("AI_CONFIG_GDRIVE_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("AI_CONFIG_GDRIVE_CLIENT_SECRET", "dummy-secret")
    seen: dict[str, bytes] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return (
                b'{"access_token": "new_acc", "expires_in": 3600}'
            )

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def mock_urlopen(req: Any, timeout: float = 30) -> FakeResponse:
        seen["body"] = req.data
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    refresh_access_token("ref_1")
    assert b"client_secret=dummy-secret" in seen["body"]


def test_client_id_constant_is_empty_in_source() -> None:
    assert GDRIVE_CLIENT_ID == ""


def test_get_client_id_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CONFIG_GDRIVE_CLIENT_ID", "test-client-id-123.apps.googleusercontent.com")
    assert get_client_id() == "test-client-id-123.apps.googleusercontent.com"


def test_get_client_id_raises_error_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_CONFIG_GDRIVE_CLIENT_ID", raising=False)
    with pytest.raises(GDriveAuthError) as exc_info:
        get_client_id()
    assert "此建置未包含 Google 登入" in str(exc_info.value)


def test_pkce_generation() -> None:
    verifier, challenge = generate_pkce()
    assert len(verifier) >= 43
    assert len(challenge) > 0
    assert "=" not in challenge
    assert GDRIVE_SCOPE == "https://www.googleapis.com/auth/drive.file"


def test_token_save_and_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environ = {"HOME": str(tmp_path)}
    token_data = {
        "access_token": "acc_123",
        "refresh_token": "ref_456",
        "expires_at": 2000000000,
        "scope": GDRIVE_SCOPE,
    }
    saved_path = save_token(token_data, environ)
    assert saved_path.is_file()
    if os.name != "nt":
        assert stat_mode_permissions(saved_path) == 0o600

    loaded = load_token(environ)
    assert loaded == token_data

    delete_token(environ)
    assert not saved_path.exists()
    assert load_token(environ) is None


def stat_mode_permissions(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_get_valid_access_token_unauthenticated(tmp_path: Path) -> None:
    environ = {"HOME": str(tmp_path)}
    with pytest.raises(GDriveAuthError) as exc_info:
        get_valid_access_token(environ)
    assert "尚未登入 Google Drive" in str(exc_info.value)


def test_refresh_token_failure_clears_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = {"HOME": str(tmp_path), "AI_CONFIG_GDRIVE_CLIENT_ID": "dummy"}
    save_token(
        {
            "access_token": "old_acc",
            "refresh_token": "bad_ref",
            "expires_at": 100,  # expired
            "scope": GDRIVE_SCOPE,
        },
        environ,
    )
    monkeypatch.setenv("AI_CONFIG_GDRIVE_CLIENT_ID", "dummy")

    def mock_urlopen(req: Any, timeout: float = 30) -> Any:
        raise urllib.error.HTTPError(
            url="http://example.com",
            code=400,
            msg="Bad Request",
            hdrs={},  # type: ignore[arg-type]
            fp=None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    with pytest.raises(GDriveAuthError) as exc_info:
        get_valid_access_token(environ)

    assert "Google Drive 授權已失效或過期" in str(exc_info.value)
    assert load_token(environ) is None


def test_stale_appdata_token_forces_relogin(tmp_path: Path) -> None:
    environ = {"HOME": str(tmp_path), "AI_CONFIG_GDRIVE_CLIENT_ID": "dummy"}
    save_token(
        {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_at": 4_000_000_000,
            "scope": "https://www.googleapis.com/auth/drive.appdata",
        },
        environ,
    )

    with pytest.raises(GDriveAuthError) as exc_info:
        get_valid_access_token(environ)

    assert "重新登入" in str(exc_info.value)
    assert load_token(environ) is None


def test_scope_depends_on_space() -> None:
    from ai_config.gdrive_auth import (
        SCOPE_HIDDEN,
        SCOPE_VISIBLE,
        scope_for_space,
    )

    assert scope_for_space("visible") == SCOPE_VISIBLE
    assert scope_for_space("hidden") == SCOPE_HIDDEN
    assert SCOPE_VISIBLE == "https://www.googleapis.com/auth/drive.file"
    assert SCOPE_HIDDEN == "https://www.googleapis.com/auth/drive.appdata"


def test_token_scope_check_follows_configured_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config.gdrive_auth import (
        SCOPE_HIDDEN,
        SCOPE_VISIBLE,
        token_has_scope,
    )

    environ = {
        "HOME": str(tmp_path),
        "AI_CONFIG_CONFIG": os.environ["AI_CONFIG_CONFIG"],
    }
    save_data_repo(tmp_path / "repo", remote_provider="gdrive", gdrive_space="hidden")

    assert token_has_scope({"scope": SCOPE_HIDDEN}, environ) is True
    # 設定是隱藏空間,但 token 只有可見空間的 scope:不算通過
    assert token_has_scope({"scope": SCOPE_VISIBLE}, environ) is False
