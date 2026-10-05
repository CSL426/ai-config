"""What the home page and push know about where this machine stands."""

import subprocess
from pathlib import Path

import pytest

from ai_config import applied_state, paths
from ai_config.gui_api import GuiApi


def _repo(root: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    for tool in ("claude", "codex", "agy"):
        (root / tool).mkdir()
    return root


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _repo(tmp_path / "data")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(applied_state, "SCRIPT_DIR", root)
    monkeypatch.setattr(paths, "SCRIPT_DIR", root)
    monkeypatch.setattr(paths, "CONFIG_ERROR", "")
    return root


def test_an_empty_repository_is_not_mistaken_for_saved_configuration(repo: Path) -> None:
    (repo / "claude/.gitkeep").write_text("", encoding="utf-8")
    assert not applied_state.repo_has_config(repo)
    (repo / "codex/config.toml").write_text('model = "x"\n', encoding="utf-8")
    assert applied_state.repo_has_config(repo)


def test_a_record_is_kept_per_repository(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not applied_state.has_record()
    applied_state.record(["claude", "memory"])  # memory 不是工具,不記
    assert applied_state.has_record()
    assert applied_state.newer_commits("claude") == []
    # 換到另一個資料庫,舊庫的紀錄不算數
    other = _repo(repo.parent / "other")
    monkeypatch.setattr(applied_state, "SCRIPT_DIR", other)
    assert not applied_state.has_record()


def test_the_home_page_learns_what_a_new_machine_still_has_to_do(repo: Path) -> None:
    info = GuiApi().onboarding_info()
    assert info["configured"] is True
    assert info["repo_empty"] is True
    assert info["applied"] is False

    (repo / "claude/CLAUDE.md").write_text("rules\n", encoding="utf-8")
    applied_state.record(["claude"])
    info = GuiApi().onboarding_info()
    assert info["repo_empty"] is False
    assert info["applied"] is True
