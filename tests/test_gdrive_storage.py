"""The Drive folder acg syncs through: where it lives, lookup, upload, retries."""

import json
import os
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any, Self

import pytest
from gdrive_test_helpers import _isolated_config  # noqa: F401

from ai_config.config import (
    ConfigError,
    configured_gdrive_space,
    normalize_gdrive_folder,
    normalize_gdrive_space,
    save_data_repo,
)
from ai_config.gdrive_auth import (
    GDRIVE_SCOPE,
    GDriveError,
    save_token,
)
from ai_config.gdrive_client import GDriveClient, make_drive_request


def test_head_info_rejects_invalid_remote_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GDriveClient()
    monkeypatch.setattr(client, "find_file", lambda name: {"id": "head"})
    monkeypatch.setattr(client, "download_file_bytes", lambda file_id: b"{}")

    with pytest.raises(GDriveError, match="unsupported format"):
        client.get_head_info()


def test_drive_request_retries_403_three_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ = {
        "HOME": str(tmp_path),
        "AI_CONFIG_GDRIVE_CLIENT_ID": "dummy",
    }
    save_token(
        {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_at": 4_000_000_000,
            "scope": GDRIVE_SCOPE,
        },
        environ,
    )
    calls = 0

    class Response:
        status = 200

        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: Any, timeout: float = 60) -> Any:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "rate limited",
                {},
                BytesIO(b"rate limited"),
            )
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("ai_config.gdrive_client.time.sleep", lambda delay: None)

    status, _, _ = make_drive_request("https://example.invalid", environ=environ)
    assert status == 200
    assert calls == 4


def _drive_responder(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> list[tuple[str, str, bytes | None]]:
    calls: list[tuple[str, str, bytes | None]] = []

    def fake_request(
        url: str,
        method: str = "GET",
        headers: Any = None,
        data: Any = None,
        environ: Any = None,
    ) -> tuple[int, dict[str, str], bytes]:
        calls.append((method, url, data))
        return 200, {}, handler(method, url, data)

    monkeypatch.setattr("ai_config.gdrive_client.make_drive_request", fake_request)
    return calls


def test_folder_is_created_in_my_drive_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(method: str, url: str, data: Any) -> bytes:
        if method == "GET":
            return b'{"files": []}'
        return b'{"id": "folder_new"}'

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient()

    assert client.folder_path == "ai-config"
    assert client.get_folder_id() == "folder_new"
    assert client.folder_url() == "https://drive.google.com/drive/folders/folder_new"
    # 第二次直接用快取,不再打 API
    assert client.get_folder_id() == "folder_new"

    assert [method for method, _, _ in calls] == ["GET", "POST"]
    list_url = calls[0][1]
    assert "spaces=appDataFolder" not in list_url
    assert "mimeType%3D%27application%2Fvnd.google-apps.folder%27" in list_url
    assert "name%3D%27ai-config%27" in list_url
    assert "%27root%27+in+parents" in list_url
    created = json.loads(calls[1][2])
    assert created == {
        "name": "ai-config",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root"],
    }


def test_nested_folder_path_is_walked_and_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(method: str, url: str, data: Any) -> bytes:
        if method == "GET" and "name%3D%27Backups%27" in url:
            return b'{"files": [{"id": "backups_id", "name": "Backups"}]}'
        if method == "GET":
            return b'{"files": []}'
        return b'{"id": "leaf_id"}'

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient(folder_path="Backups/ai-config")

    assert client.get_folder_id() == "leaf_id"
    assert [method for method, _, _ in calls] == ["GET", "GET", "POST"]
    assert "%27root%27+in+parents" in calls[0][1]
    assert "%27backups_id%27+in+parents" in calls[1][1]
    assert json.loads(calls[2][2])["parents"] == ["backups_id"]


def test_saved_folder_id_wins_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = {
        "HOME": str(tmp_path),
        "AI_CONFIG_GDRIVE_CLIENT_ID": "dummy",
        "AI_CONFIG_CONFIG": os.environ["AI_CONFIG_CONFIG"],
    }
    save_data_repo(
        tmp_path / "repo",
        remote_provider="gdrive",
        gdrive_folder="Old/Name",
        gdrive_folder_id="saved_id",
    )

    def handler(method: str, url: str, data: Any) -> bytes:
        assert method == "GET" and "/files/saved_id?" in url
        return (
            b'{"id": "saved_id", "trashed": false, '
            b'"mimeType": "application/vnd.google-apps.folder"}'
        )

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient(environ)

    assert client.folder_path == "Old/Name"
    assert client.get_folder_id() == "saved_id"
    assert len(calls) == 1


def test_trashed_saved_folder_falls_back_to_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = {
        "HOME": str(tmp_path),
        "AI_CONFIG_GDRIVE_CLIENT_ID": "dummy",
        "AI_CONFIG_CONFIG": os.environ["AI_CONFIG_CONFIG"],
    }
    save_data_repo(
        tmp_path / "repo",
        remote_provider="gdrive",
        gdrive_folder="ai-config",
        gdrive_folder_id="gone_id",
    )

    def handler(method: str, url: str, data: Any) -> bytes:
        if "/files/gone_id?" in url:
            return b'{"id": "gone_id", "trashed": true}'
        if method == "GET":
            return b'{"files": []}'
        return b'{"id": "recreated"}'

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient(environ)

    assert client.get_folder_id() == "recreated"
    assert [method for method, _, _ in calls] == ["GET", "GET", "POST"]


def test_normalize_gdrive_folder() -> None:
    assert normalize_gdrive_folder(None) == "ai-config"
    assert normalize_gdrive_folder("   ") == "ai-config"
    assert normalize_gdrive_folder("/Backups//ai-config/") == "Backups/ai-config"
    assert normalize_gdrive_folder("Backups\\acg") == "Backups/acg"
    with pytest.raises(ConfigError):
        normalize_gdrive_folder("../x")


def test_files_are_scoped_to_existing_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(method: str, url: str, data: Any) -> bytes:
        if "vnd.google-apps.folder" in url:
            return b'{"files": [{"id": "folder_1", "name": "ai-config"}]}'
        if method == "GET" and "repo.bundle" in url:
            return b'{"files": [{"id": "bundle_1", "name": "repo.bundle"}]}'
        if method == "GET":
            return b'{"files": []}'
        return b'{"id": "created", "headRevisionId": "rev"}'

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient()

    found = client.find_file("repo.bundle")
    assert found is not None and found["id"] == "bundle_1"
    assert "%27folder_1%27+in+parents" in calls[-1][1]

    client.upload_file("head.json", b"{}", content_type="application/json")
    multipart = calls[-1][2]
    assert b'"parents": ["folder_1"]' in multipart
    assert b"appDataFolder" not in multipart


def test_normalize_gdrive_space() -> None:
    assert normalize_gdrive_space(None) == "visible"
    assert normalize_gdrive_space("visible") == "visible"
    assert normalize_gdrive_space(" HIDDEN ") == "hidden"
    with pytest.raises(ConfigError):
        normalize_gdrive_space("elsewhere")


def test_hidden_space_uses_appdatafolder_without_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = {
        "HOME": str(tmp_path),
        "AI_CONFIG_GDRIVE_CLIENT_ID": "dummy",
        "AI_CONFIG_CONFIG": os.environ["AI_CONFIG_CONFIG"],
    }
    save_data_repo(tmp_path / "repo", remote_provider="gdrive", gdrive_space="hidden")
    assert configured_gdrive_space(environ) == "hidden"

    def handler(method: str, url: str, data: Any) -> bytes:
        return b'{"files": []}'

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient(environ)

    assert client.hidden is True
    # 隱藏空間直接用別名,不必查詢或建立資料夾
    assert client.get_folder_id() == "appDataFolder"
    assert calls == []
    assert client.folder_url() == ""

    client.find_file("repo.bundle")
    assert "spaces=appDataFolder" in calls[-1][1]


def test_visible_space_never_sets_spaces_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(method: str, url: str, data: Any) -> bytes:
        if "vnd.google-apps.folder" in url:
            return b'{"files": [{"id": "f1", "name": "ai-config"}]}'
        return b'{"files": []}'

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient(space="visible")

    client.find_file("repo.bundle")
    assert all("spaces=appDataFolder" not in url for _, url, _ in calls)


def test_hidden_space_uploads_into_appdatafolder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(method: str, url: str, data: Any) -> bytes:
        if method == "GET":
            return b'{"files": []}'
        return b'{"id": "new", "headRevisionId": "rev"}'

    calls = _drive_responder(monkeypatch, handler)
    client = GDriveClient(space="hidden")

    client.upload_file("head.json", b"{}", content_type="application/json")
    assert b'"parents": ["appDataFolder"]' in calls[-1][2]


def test_location_label_describes_each_space() -> None:
    visible = GDriveClient(space="visible", folder_path="Backups/acg")
    hidden = GDriveClient(space="hidden")

    assert visible.location_label() == "我的雲端硬碟/Backups/acg"
    assert "appDataFolder" in hidden.location_label()
