"""Catching up with the remote before an unattended push, on real repositories."""

import subprocess
from pathlib import Path

import pytest

from ai_config import autopush

JOURNAL = "memory/projects/demo/journal/today.md"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def _clone(remote: Path, path: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(path)], check=True)
    _git(path, "config", "user.name", "t")
    _git(path, "config", "user.email", "t@t")
    return path


@pytest.fixture
def machines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "tuple[Path, Path]":
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    seed = _clone(remote, tmp_path / "seed")
    (seed / JOURNAL).parent.mkdir(parents=True)
    (seed / JOURNAL).write_text("## 00:27\nshared start\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "init")
    _git(seed, "push", "-q", "origin", "HEAD")
    mine = _clone(remote, tmp_path / "mine")
    other = _clone(remote, tmp_path / "other")
    monkeypatch.setattr(autopush, "SCRIPT_DIR", mine)
    return mine, other


def _other_machine_appends(other: Path, text: str) -> None:
    journal = other / JOURNAL
    journal.write_text(journal.read_text(encoding="utf-8") + text, encoding="utf-8")
    _git(other, "commit", "-qam", "other machine")
    _git(other, "push", "-q", "origin", "HEAD")


def test_a_conflict_leaves_the_repository_exactly_as_it_was(machines) -> None:
    # 兩台在同一份日誌的同一處各記一筆:接上時套回本機改動會衝突。
    # 以前會把衝突標記留在工作區、改動留在 stash,之後每次 pull 都失敗
    mine, other = machines
    _other_machine_appends(other, "## 09:34\nfrom the other machine\n")
    local = "## 00:27\nshared start\n## 00:32\nfrom this machine\n"
    (mine / JOURNAL).write_text(local, encoding="utf-8")
    _git(mine, "fetch", "-q")
    head = _git(mine, "rev-parse", "HEAD")

    assert autopush._catch_up() is False

    assert (mine / JOURNAL).read_text(encoding="utf-8") == local
    assert _git(mine, "rev-parse", "HEAD") == head
    assert _git(mine, "diff", "--name-only", "--diff-filter=U") == ""
    assert _git(mine, "stash", "list") == ""


def test_changes_elsewhere_are_caught_up_with_local_notes_kept(machines) -> None:
    mine, other = machines
    _other_machine_appends(other, "## 09:34\nfrom the other machine\n")
    note = mine / "memory/topics/new.md"
    note.parent.mkdir(parents=True)
    note.write_text("a local note\n", encoding="utf-8")
    _git(mine, "fetch", "-q")

    assert autopush._catch_up() is True

    assert "from the other machine" in (mine / JOURNAL).read_text(encoding="utf-8")
    assert note.read_text(encoding="utf-8") == "a local note\n"
    assert _git(mine, "rev-list", "--count", "HEAD..@{upstream}") == "0"


def test_a_stalled_push_is_reported_not_just_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 一台因此三天沒同步,卻只在日誌裡留一行「跳過」
    root = tmp_path / "memory"
    root.mkdir()
    monkeypatch.setattr(autopush, "memory_dir", lambda: root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: True)
    monkeypatch.setattr(autopush, "_catch_up", lambda: False)
    monkeypatch.setattr(autopush, "_memory_has_changes", lambda: True)

    assert autopush.decide().push is False

    failure = autopush.last_failure()
    assert failure is not None and "無法自動接上" in failure["reason"]
