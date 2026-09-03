"""Unit tests for Google Drive sync provider (ai_config/gdrive.py)."""

import json
import os
import subprocess
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any, Self

import pytest

from ai_config.commands.setup import SetupError, setup_gdrive_repository
from ai_config.config import (
    ConfigError,
    configured_remote_provider,
    load_config,
    normalize_gdrive_folder,
    save_data_repo,
)
from ai_config.gdrive import (
    GDRIVE_CLIENT_ID,
    GDRIVE_CLIENT_SECRET,
    GDRIVE_SCOPE,
    GDriveAuthError,
    GDriveClient,
    GDriveError,
    delete_token,
    gdrive_pull,
    gdrive_push_upload,
    generate_pkce,
    get_client_id,
    get_valid_access_token,
    load_token,
    make_drive_request,
    save_token,
)
from ai_config.paths import EXCLUDED_FILES


class _MockDriveClient:
    folder_path = "ai-config"

    def __init__(self, environ: Any = None, **kwargs: Any) -> None:
        pass


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # CI runner 會設 XDG_CONFIG_HOME(Linux)/APPDATA(Windows),只 patch HOME
    # 不夠:config 會寫進 runner 的真實目錄並汙染後續 subprocess 測試。
    # AI_CONFIG_CONFIG 是 config_path() 的最高優先,直接鎖到本測試的 tmp。
    monkeypatch.setenv(
        "AI_CONFIG_CONFIG", str(tmp_path / "isolated" / "config.json")
    )
    monkeypatch.delenv("AI_CONFIG_REPO", raising=False)


def test_token_file_in_excluded_files() -> None:
    assert "gdrive_token.json" in EXCLUDED_FILES


def test_client_secret_constant_is_empty_in_source() -> None:
    assert GDRIVE_CLIENT_SECRET == ""


def test_refresh_includes_client_secret_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.gdrive import refresh_access_token

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


def test_remote_provider_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_CONFIG_PROVIDER", "other")
    with pytest.raises(ConfigError, match="git or gdrive"):
        configured_remote_provider()


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


def test_setup_gdrive_verification_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_CONFIG_GDRIVE_CLIENT_ID", "dummy")

    save_token(
        {
            "access_token": "mock_token",
            "refresh_token": "mock_refresh",
            "expires_at": 2000000000,
            "scope": GDRIVE_SCOPE,
        },
    )

    steps: list[str] = []

    class MockDriveClient(_MockDriveClient):

        def verify_setup_access(self) -> str:
            steps.append("verified")
            return "https://drive.google.com/drive/folders/folder_abc"

        def get_folder_id(self) -> str:
            return "folder_abc"

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    setup_gdrive_repository(data_dir, " Backups / ai-config ")

    assert steps == ["verified"]
    assert (data_dir / ".git").is_dir()
    assert (data_dir / "claude").is_dir()
    assert (data_dir / "codex").is_dir()
    assert (data_dir / "agy").is_dir()

    cfg = load_config()
    assert cfg["remote_provider"] == "gdrive"
    assert cfg["gdrive_folder"] == "Backups/ai-config"
    assert cfg["gdrive_folder_id"] == "folder_abc"


def test_setup_gdrive_verification_failure_does_not_save_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_CONFIG_GDRIVE_CLIENT_ID", "dummy")

    save_token(
        {
            "access_token": "mock_token",
            "refresh_token": "mock_refresh",
            "expires_at": 2000000000,
            "scope": GDRIVE_SCOPE,
        },
    )

    class FailingDriveClient:
        def __init__(self, environ: Any = None, **kwargs: Any) -> None:
            pass

        def verify_setup_access(self) -> None:
            raise GDriveError("Setup verification test failed")

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", FailingDriveClient)

    with pytest.raises(SetupError) as exc_info:
        setup_gdrive_repository(data_dir)

    assert "Google Drive setup failed" in str(exc_info.value)
    assert not (tmp_path / "isolated" / "config.json").exists()


def init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "checkout", "-B", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    (path / "claude").mkdir(exist_ok=True)
    (path / "claude/settings.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )
    res = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def test_gdrive_pull_empty_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return None

        def find_file(self, name: str) -> Any:
            return None

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    ret = gdrive_pull(repo_dir, "all")
    assert ret == 1
    captured = capsys.readouterr()
    assert "遠端為空" in captured.err or "遠端為空" in captured.out


def test_gdrive_pull_already_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    head_sha = init_git_repo(repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return {"commit": head_sha}

        def find_file(self, name: str) -> Any:
            return None

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    ret = gdrive_pull(repo_dir, "all")
    assert ret == 0
    captured = capsys.readouterr()
    assert "already up to date" in captured.out


def test_gdrive_pull_fast_forwardable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    remote_repo = tmp_path / "remote"
    init_git_repo(remote_repo)

    local_repo = tmp_path / "local"
    subprocess.run(["git", "clone", str(remote_repo), str(local_repo)], check=True, capture_output=True)

    (remote_repo / "claude/test.txt").write_text("update", encoding="utf-8")
    subprocess.run(["git", "-C", str(remote_repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(remote_repo), "commit", "-m", "second commit"],
        check=True,
        capture_output=True,
    )
    new_remote_sha = subprocess.run(
        ["git", "-C", str(remote_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    bundle_path = tmp_path / "test.bundle"
    subprocess.run(
        ["git", "-C", str(remote_repo), "bundle", "create", str(bundle_path), "main"],
        check=True,
        capture_output=True,
    )
    bundle_bytes = bundle_path.read_bytes()

    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", local_repo)

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return {"commit": new_remote_sha}

        def find_file(self, name: str) -> Any:
            return {"id": "bundle_123", "name": "repo.bundle"}

        def download_file_bytes(self, file_id: str) -> bytes:
            return bundle_bytes

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    ret = gdrive_pull(local_repo, "all")
    assert ret == 0

    local_head = subprocess.run(
        ["git", "-C", str(local_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert local_head == new_remote_sha


def test_gdrive_push_upload_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    head_sha = init_git_repo(repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    uploaded: dict[str, Any] = {}

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return None

        def find_file(self, name: str) -> Any:
            return {"id": "bundle_123", "headRevisionId": "rev_1"}

        def upload_file(self, name: str, content: bytes, file_id: Any = None, content_type: str = "") -> Any:
            uploaded[name] = content
            return {"id": "file_id", "headRevisionId": "rev_2"}

        def get_file_metadata(self, file_id: str) -> Any:
            return {"id": file_id, "headRevisionId": "rev_2"}

        def update_head_info(self, commit_sha: str) -> Any:
            uploaded["head_commit"] = commit_sha
            return {}

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    ret = gdrive_push_upload(repo_dir)
    assert ret == 0
    assert "repo.bundle" in uploaded
    assert uploaded["head_commit"] == head_sha


def test_gdrive_push_upload_diverged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return {"commit": "0000000000000000000000000000000000000000"}

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    ret = gdrive_push_upload(repo_dir)
    assert ret == 1
    captured = capsys.readouterr()
    assert "diverged" in captured.err or "較新的提交" in captured.err


def test_gdrive_pull_rejects_diverged_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote_repo = tmp_path / "remote"
    init_git_repo(remote_repo)
    local_repo = tmp_path / "local"
    subprocess.run(
        ["git", "clone", str(remote_repo), str(local_repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local_repo), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(local_repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (local_repo / "claude/local.txt").write_text("local", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(local_repo), "add", "."],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local_repo), "commit", "-m", "local"],
        check=True,
        capture_output=True,
    )
    local_head = subprocess.run(
        ["git", "-C", str(local_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    (remote_repo / "claude/remote.txt").write_text("remote", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(remote_repo), "add", "."],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(remote_repo), "commit", "-m", "remote"],
        check=True,
        capture_output=True,
    )
    remote_head = subprocess.run(
        ["git", "-C", str(remote_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bundle = tmp_path / "remote.bundle"
    subprocess.run(
        ["git", "-C", str(remote_repo), "bundle", "create", str(bundle), "main"],
        check=True,
        capture_output=True,
    )

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return {"commit": remote_head, "format": 1}

        def find_file(self, name: str) -> Any:
            return {"id": "bundle"}

        def download_file_bytes(self, file_id: str) -> bytes:
            return bundle.read_bytes()

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    assert gdrive_pull(local_repo, "all") == 1
    assert subprocess.run(
        ["git", "-C", str(local_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == local_head
    assert "not safe to fast-forward" in capsys.readouterr().err


def test_gdrive_pull_rejects_bundle_head_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote_repo = tmp_path / "remote"
    init_git_repo(remote_repo)
    local_repo = tmp_path / "local"
    subprocess.run(
        ["git", "clone", str(remote_repo), str(local_repo)],
        check=True,
        capture_output=True,
    )
    (remote_repo / "claude/remote.txt").write_text("remote", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(remote_repo), "add", "."],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(remote_repo), "commit", "-m", "remote"],
        check=True,
        capture_output=True,
    )
    bundle = tmp_path / "remote.bundle"
    subprocess.run(
        ["git", "-C", str(remote_repo), "bundle", "create", str(bundle), "main"],
        check=True,
        capture_output=True,
    )

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return {"commit": "f" * 40, "format": 1}

        def find_file(self, name: str) -> Any:
            return {"id": "bundle"}

        def download_file_bytes(self, file_id: str) -> bytes:
            return bundle.read_bytes()

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    assert gdrive_pull(local_repo, "all") == 1
    assert "does not match head.json" in capsys.readouterr().err


def test_gdrive_push_rechecks_uploaded_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    head_updated = False

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return None

        def find_file(self, name: str) -> Any:
            return {"id": "bundle", "headRevisionId": "rev_1"}

        def upload_file(
            self,
            name: str,
            content: bytes,
            file_id: Any = None,
            content_type: str = "",
        ) -> Any:
            return {"id": "bundle", "headRevisionId": "rev_2"}

        def get_file_metadata(self, file_id: str) -> Any:
            return {"id": file_id, "headRevisionId": "competing_revision"}

        def update_head_info(self, commit_sha: str) -> Any:
            nonlocal head_updated
            head_updated = True
            return {}

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    assert gdrive_push_upload(repo_dir) == 1
    assert not head_updated


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
    monkeypatch.setattr("ai_config.gdrive.time.sleep", lambda delay: None)

    status, _, _ = make_drive_request("https://example.invalid", environ=environ)
    assert status == 200
    assert calls == 4


def test_gdrive_preflight_counts_commits_when_remote_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import push as push_cmd

    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    monkeypatch.setenv("AI_CONFIG_PROVIDER", "gdrive")
    monkeypatch.setattr(push_cmd, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return None

    monkeypatch.setattr("ai_config.gdrive.GDriveClient", MockDriveClient)

    preflight = push_cmd._push_preflight(["claude"])
    assert preflight is not None
    assert preflight.ahead == 1
    assert not preflight.has_changes


def test_working_paths_scans_unborn_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import push as push_cmd

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(
        ["git", "-C", str(repo_dir), "init", "-b", "main"],
        check=True,
        capture_output=True,
    )
    (repo_dir / "claude").mkdir()
    (repo_dir / "claude/CLAUDE.md").write_text("hi\n", encoding="utf-8")
    (repo_dir / "claude/settings.json").write_text("{}", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_dir), "add", "claude/CLAUDE.md"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(push_cmd, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    # unborn HEAD:索引中與未追蹤的檔案都要被列出,而不是 fatal: bad revision
    assert push_cmd._working_paths() == [
        "claude/CLAUDE.md",
        "claude/settings.json",
    ]


def test_unstage_tools_works_on_unborn_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import push as push_cmd

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(
        ["git", "-C", str(repo_dir), "init", "-b", "main"],
        check=True,
        capture_output=True,
    )
    (repo_dir / "claude").mkdir()
    (repo_dir / "claude/CLAUDE.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_dir), "add", "claude"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(push_cmd, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    assert push_cmd._unstage_tools(["claude"]) is True
    staged = subprocess.run(
        ["git", "-C", str(repo_dir), "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert staged.stdout.strip() == ""


def test_allow_secrets_flag_bypasses_credential_content_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import push as push_cmd

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(
        ["git", "-C", str(repo_dir), "init", "-b", "main"],
        check=True,
        capture_output=True,
    )
    (repo_dir / "claude").mkdir()
    # 教學文件常見的假金鑰:會觸發 _SECRET_PATTERN,但不是真憑證
    (repo_dir / "claude/security.md").write_text(
        'example: api_key = "not-a-real-key"\n', encoding="utf-8"
    )
    # 行尾空白:內容檔常見,只該警告不該擋 push
    (repo_dir / "claude/notes.md").write_text(
        "trailing space here \n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "add", "claude"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(push_cmd, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    monkeypatch.setattr(push_cmd, "_ALLOW_SECRET_PATHS", False)
    assert push_cmd._validate_staged_push(["claude"]) is False

    monkeypatch.setattr(push_cmd, "_ALLOW_SECRET_PATHS", True)
    assert push_cmd._validate_staged_push(["claude"]) is True


def test_gdrive_can_create_first_commit_in_unborn_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import push as push_cmd

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(
        ["git", "-C", str(repo_dir), "init", "-b", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"],
        check=True,
    )
    settings = repo_dir / "claude/settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_dir), "add", "."],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("AI_CONFIG_PROVIDER", "gdrive")
    monkeypatch.setattr(push_cmd, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.gdrive.gdrive_push_upload", lambda path: 0)
    reviewed_diff = push_cmd._staged_diff()
    assert reviewed_diff is not None

    assert push_cmd._commit_and_push("chore: initial config", ["claude"], reviewed_diff) == 0
    assert subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).returncode == 0


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

    monkeypatch.setattr("ai_config.gdrive.make_drive_request", fake_request)
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
