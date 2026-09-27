"""Pulling and pushing the data repository through Google Drive, and its setup."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from gdrive_test_helpers import _isolated_config  # noqa: F401
from push_helpers import patch_push

from ai_config import push_preflight, push_publish
from ai_config.commands.setup_gdrive import setup_gdrive_repository
from ai_config.commands.setup_git import SetupError
from ai_config.config import (
    ConfigError,
    configured_remote_provider,
    load_config,
)
from ai_config.gdrive_auth import (
    GDRIVE_SCOPE,
    GDriveError,
    save_token,
)
from ai_config.gdrive_sync import gdrive_pull, gdrive_push_upload


class _MockDriveClient:
    folder_path = "ai-config"
    space = "visible"
    hidden = False

    def __init__(self, environ: Any = None, **kwargs: Any) -> None:
        pass

    def location_label(self) -> str:
        return f"我的雲端硬碟/{self.folder_path}"


def test_remote_provider_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_CONFIG_PROVIDER", "other")
    with pytest.raises(ConfigError, match="git or gdrive"):
        configured_remote_provider()


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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

        def location_label(self) -> str:
            return "我的雲端硬碟/ai-config"

        def verify_setup_access(self) -> None:
            raise GDriveError("Setup verification test failed")

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", FailingDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

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

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

    assert gdrive_push_upload(repo_dir) == 1
    assert not head_updated


def test_gdrive_preflight_counts_commits_when_remote_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    repo_dir = tmp_path / "repo"
    init_git_repo(repo_dir)
    monkeypatch.setenv("AI_CONFIG_PROVIDER", "gdrive")
    patch_push(monkeypatch, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    class MockDriveClient(_MockDriveClient):

        def get_head_info(self) -> Any:
            return None

    monkeypatch.setattr("ai_config.gdrive_client.GDriveClient", MockDriveClient)

    preflight = push_preflight._push_preflight(["claude"])
    assert preflight is not None
    assert preflight.ahead == 1
    assert not preflight.has_changes


def test_working_paths_scans_unborn_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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
    patch_push(monkeypatch, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    # unborn HEAD:索引中與未追蹤的檔案都要被列出,而不是 fatal: bad revision
    assert push_preflight._working_paths() == [
        "claude/CLAUDE.md",
        "claude/settings.json",
    ]


def test_unstage_tools_works_on_unborn_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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
    patch_push(monkeypatch, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    assert push_publish._unstage_tools(["claude"]) is True
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
    patch_push(monkeypatch, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)

    patch_push(monkeypatch, "_ALLOW_SECRET_PATHS", False)
    assert push_preflight._validate_staged_push(["claude"]) is False

    patch_push(monkeypatch, "_ALLOW_SECRET_PATHS", True)
    assert push_preflight._validate_staged_push(["claude"]) is True


def test_gdrive_can_create_first_commit_in_unborn_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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
    patch_push(monkeypatch, "SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.commands.sync.SCRIPT_DIR", repo_dir)
    monkeypatch.setattr("ai_config.gdrive_sync.gdrive_push_upload", lambda path: 0)
    reviewed_diff = push_preflight._staged_diff()
    assert reviewed_diff is not None

    assert push_publish._commit_and_push("chore: initial config", ["claude"], reviewed_diff) == 0
    assert subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).returncode == 0
