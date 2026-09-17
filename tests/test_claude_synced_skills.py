"""Claude Code's own skills/synced/ cache is not ours to store or delete."""

from pathlib import Path

import pytest
from test_apply_projection import write

from ai_config import paths
from ai_config.fsops import mirror_dir, overlay_dir_to_stage
from ai_config.paths import CLAUDE_VENDOR_SKILL_DIRS


def _skills(root: Path) -> Path:
    write(root / "mine/SKILL.md", "user skill\n")
    # Claude Code downloads these itself and refreshes them on its own.
    write(root / "synced/abc-123/pdf/SKILL.md", "first-party skill\n")
    return root


def test_mirror_leaves_the_vendor_cache_out_of_the_repo(tmp_path: Path) -> None:
    live = _skills(tmp_path / "live")
    repo = tmp_path / "repo"

    mirror_dir(live, repo, exclude_dir_names=CLAUDE_VENDOR_SKILL_DIRS)

    assert (repo / "mine/SKILL.md").is_file()
    assert not (repo / "synced").exists()


def test_mirror_never_deletes_the_live_cache(tmp_path: Path) -> None:
    """apply mirrors exactly, so an unexcluded cache would be wiped."""
    live = _skills(tmp_path / "live")
    repo = tmp_path / "repo"
    write(repo / "mine/SKILL.md", "user skill\n")

    mirror_dir(repo, live, exclude_dir_names=CLAUDE_VENDOR_SKILL_DIRS)

    assert (live / "synced/abc-123/pdf/SKILL.md").is_file()


def test_stage_overlay_skips_it_too(tmp_path: Path) -> None:
    live = _skills(tmp_path / "live")
    stage = tmp_path / "stage"

    overlay_dir_to_stage(live, stage, exclude_dir_names=CLAUDE_VENDOR_SKILL_DIRS)

    assert (stage / "mine/SKILL.md").is_file()
    assert not (stage / "synced").exists()


def test_status_does_not_offer_to_remove_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import status as command

    live = tmp_path / "home"
    stage = tmp_path / "repo"
    _skills(live / "skills")
    write(stage / "skills/mine/SKILL.md", "user skill\n")
    monkeypatch.setattr(paths, "CLAUDE_HOME", live)

    removals = command._planned_removals("claude", stage, live)

    assert all("synced" not in path.as_posix() for path in removals), removals
