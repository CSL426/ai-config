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
