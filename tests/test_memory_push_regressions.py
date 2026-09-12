"""Exercise memory push boundaries using disposable Git repositories."""

import subprocess
from pathlib import Path

import pytest

from ai_config.commands import push, sync


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def data_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=main")
    repo = tmp_path / "data"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "claude").mkdir()
    (repo / "claude/settings.json").write_text("{}\n", encoding="utf-8")
    git(repo, "add", "claude")
    git(repo, "commit", "-m", "Initial configuration")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--set-upstream", "origin", "main")
    monkeypatch.setattr(push, "SCRIPT_DIR", repo)
    monkeypatch.setattr(sync, "SCRIPT_DIR", repo)
    monkeypatch.setattr(push, "configured_remote_provider", lambda: "git")
    monkeypatch.setattr(push, "_remote_is_read_only", lambda: False)
    monkeypatch.setattr(push, "_ALLOW_SECRET_PATHS", False)
    return repo


def tracked_memory(repo: Path) -> Path:
    root = repo / "memory"
    root.mkdir()
    (root / "MEMORY.md").write_text("# Notes\n", encoding="utf-8")
    git(repo, "add", "memory")
    git(repo, "commit", "-m", "Add notes")
    git(repo, "push")
    return root


def test_push_all_without_optional_directories_can_review_and_cancel(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme": "dark"}\n', encoding="utf-8")
    reviewed = []

    def decline(prompt: str) -> bool:
        reviewed.append(git(data_repo, "diff", "--cached", "--name-only"))
        return False

    monkeypatch.setattr(push, "confirm_prompt", decline)
    # 沒拿到確認就沒推,離開碼要讓呼叫端分得出來
    assert push.do_push("all") == 1
    assert reviewed == ["claude/settings.json"]
    assert git(data_repo, "diff", "--cached", "--name-only") == ""
    assert git(data_repo, "diff", "--name-only") == "claude/settings.json"
    assert not (data_repo / "memory").exists()


def test_nothing_to_push_is_a_no_op_not_a_failure(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """暫存後沒有實質差異(例如純換行變動)是 no-op,不是失敗。"""
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme": "dark"}\n', encoding="utf-8")
    # git status 看得到修改,但暫存差異是空的
    monkeypatch.setattr(push, "_staged_diff", lambda: "")

    assert push.do_push("claude") == 0


@pytest.mark.parametrize("scope", ["memory", "all"])
def test_missing_memory_root_refuses_push_before_staging(
    data_repo: Path, scope: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").unlink()
    root.rmdir()

    assert push.do_push(scope) == 1
    assert "Memory directory is missing" in capsys.readouterr().err
    assert git(data_repo, "diff", "--cached", "--name-only") == ""
    assert git(data_repo, "diff", "--name-only") == "memory/MEMORY.md"


def test_memory_root_disappearing_after_preflight_refuses_staging(
    data_repo: Path,
) -> None:
    root = tracked_memory(data_repo)
    assert push._push_preflight(["memory"]) is not None
    (root / "MEMORY.md").unlink()
    root.rmdir()

    assert push._stage_push_changes(["memory"]) is None
    assert git(data_repo, "diff", "--cached", "--name-only") == ""


def test_individual_memory_deletion_allowed_while_root_exists(
    data_repo: Path,
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").unlink()

    reviewed = push._stage_push_changes(["memory"])
    assert reviewed is not None
    assert "deleted file" in reviewed
    assert push._staged_push_matches(["memory"], reviewed)

    # An empty root can vanish without changing the Git diff after review.
    root.rmdir()
    assert not push._staged_push_matches(["memory"], reviewed)
    assert push._unstage_tools(push._push_scopes("all"))
    assert git(data_repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize(
    ("relative", "content", "message"),
    [
        ("memory/auth.json", "{}\n", "Credential files"),
        ("memory/MEMORY.md", "password = example\n", "Potential credential"),
        ("notes.md", "outside\n", "Unexpected repository changes"),
    ],
)
def test_optional_scope_filter_keeps_staged_safety_checks(
    data_repo: Path,
    relative: str,
    content: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Changed notes\n", encoding="utf-8")
    (data_repo / relative).write_text(content, encoding="utf-8")

    assert push._stage_push_changes(push._push_scopes("all")) is None
    assert message in capsys.readouterr().err
    assert git(data_repo, "diff", "--cached", "--name-only") == ""


def test_memory_push_preserves_other_pre_staged_changes(
    data_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Changed notes\n", encoding="utf-8")
    (data_repo / "claude/settings.json").write_text(
        '{"theme": "dark"}\n', encoding="utf-8"
    )
    git(data_repo, "add", "claude")

    assert push.do_push("memory") == 1
    assert "pre-staged changes" in capsys.readouterr().err
    assert git(data_repo, "diff", "--cached", "--name-only") == "claude/settings.json"


def test_memory_push_refuses_unpublished_configuration_commits(
    data_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked_memory(data_repo)
    (data_repo / "claude/settings.json").write_text(
        '{"theme": "dark"}\n', encoding="utf-8"
    )
    git(data_repo, "add", "claude")
    git(data_repo, "commit", "-m", "Unpublished configuration")

    assert push.do_push("memory") == 1
    assert "outside the selected tools" in capsys.readouterr().err
    assert git(data_repo, "rev-list", "--count", "@{upstream}..HEAD") == "1"
