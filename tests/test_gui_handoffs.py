"""The memory page lists handoff threads the way `handoff list` does."""

from pathlib import Path

import pytest

from ai_config import handoff, memory
from ai_config.gui_management import _handoff_threads


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    root.mkdir()
    monkeypatch.setattr(memory, "memory_dir", lambda: root)
    monkeypatch.setattr(handoff, "memory_dir", lambda: root)
    monkeypatch.setattr(
        handoff, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t")
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "d4b49a91-38e2-4f31-900a-24d0ae63b153")
    return root


def test_live_threads_are_listed_with_what_the_cli_shows(notebook: Path) -> None:
    """The page had a reminder switch and no threads at all."""
    handoff.write("做到一半", "## Next\n- **補測試**。然後發版")
    handoff.write("接走的", "內容")
    handoff.claim("接走的")
    handoff.write("做完的", "內容")
    handoff.done("做完的")

    threads = _handoff_threads()

    # 接走就結案,只剩還沒人接的
    assert [t["thread"] for t in threads] == ["做到一半"]
    one = threads[0]
    assert one["project"] == "o--r"
    assert one["state"] == "open"
    assert "holder" not in one
    assert one["stale"] is False
    assert one["summary"] == "還剩:補測試。然後發版"


def test_a_broken_notebook_does_not_break_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(project=""):
        raise OSError("gone")

    monkeypatch.setattr(handoff, "load_all", explode)

    assert _handoff_threads() == []
