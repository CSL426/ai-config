"""The notebook walk runs on every prompt; it must stay cheap and still refuse links."""

import os
from pathlib import Path

import pytest

from ai_config import memory_paths


def _tree(root: Path, depth: int = 4) -> Path:
    leaf = root
    for level in range(depth):
        leaf = leaf / f"d{level}"
    leaf.mkdir(parents=True)
    (leaf / "note.md").write_text("x\n", encoding="utf-8")
    return leaf


def test_a_plain_tree_passes(tmp_path: Path) -> None:
    _tree(tmp_path / "projects")
    memory_paths._assert_tree(tmp_path / "projects")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_link_deep_in_the_tree_is_refused(tmp_path: Path) -> None:
    leaf = _tree(tmp_path / "projects")
    (tmp_path / "elsewhere").mkdir()
    (leaf / "sneaky").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    with pytest.raises(RuntimeError, match="reparse point"):
        memory_paths._assert_tree(tmp_path / "projects")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_an_odd_file_type_deep_in_the_tree_is_refused(tmp_path: Path) -> None:
    leaf = _tree(tmp_path / "projects")
    os.mkfifo(leaf / "pipe")

    with pytest.raises(RuntimeError, match="Unexpected path type"):
        memory_paths._assert_tree(tmp_path / "projects")


def test_each_entry_is_checked_once_not_once_per_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-checking every ancestor of every note took ~210,000 stats per prompt."""
    root = tmp_path / "projects"
    for project in range(20):
        leaf = _tree(root / f"p{project}", depth=3)
        for note in range(10):
            (leaf / f"n{note}.md").write_text("x\n", encoding="utf-8")
    calls = []
    real = memory_paths.is_reparse_point
    monkeypatch.setattr(memory_paths, "is_reparse_point", lambda p: calls.append(p) or real(p))

    memory_paths._assert_tree(root)

    entries = sum(1 for _ in root.rglob("*"))
    assert len(calls) <= entries + 40  # 每個項目一次,加上根目錄祖先的少量檢查


def test_a_file_removed_during_the_walk_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remember creates and deletes its own temp files while the hook walks."""
    leaf = _tree(tmp_path / "projects")
    gone = leaf / "save-session.pid"
    gone.write_text("1\n", encoding="utf-8")
    real = memory_paths.is_reparse_point

    def vanish(path: Path) -> bool:
        result = real(path)
        if path == gone:
            path.unlink()
        return result

    monkeypatch.setattr(memory_paths, "is_reparse_point", vanish)

    memory_paths._assert_tree(tmp_path / "projects")


def test_a_busy_lock_skips_the_repair_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path,
) -> None:
    """Another acg holding the lock is not an error worth a line on every prompt."""
    import io
    import json
    from contextlib import contextmanager

    from ai_config import locking, memory_hooks

    @contextmanager
    def busy(timeout: float = 10.0):
        raise TimeoutError("Timed out waiting for apply lock")
        yield

    monkeypatch.setattr(locking, "apply_lock", busy)
    monkeypatch.setattr(memory_hooks.memory_paths, "link_state", lambda: ("ok", ""))
    monkeypatch.setattr(memory_hooks.memory_journal, "journal_config_state", lambda: ("ours", ""))
    monkeypatch.setattr(memory_hooks.memory_paths, "has_block", lambda text: True)
    monkeypatch.setattr(memory_hooks.memory_paths, "_read_text", lambda path: "")
    payload = json.dumps({"cwd": str(tmp_path), "hook_event_name": "UserPromptSubmit"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    assert memory_hooks.run([str(memory_hooks.memory_paths.SCRIPT_DIR)]) == 0
    assert "未修復" not in capsys.readouterr().err
