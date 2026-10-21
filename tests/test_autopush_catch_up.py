"""Catching up with the remote before an unattended push, on real repositories."""

import subprocess
import sys
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


HANDOFF = "memory/handoff/acg 1.0.45 收尾.md"
ARCHIVED = "memory/handoff/archive/acg 1.0.45 收尾.md"


def _both_archive_the_same_handoff(mine: Path, other: Path, *, crlf: bool = False) -> None:
    """DESKTOP-CAO4ENQ, 2026-10-21: two machines archived one stale handoff each."""
    body = "## Goal\n收尾\n"
    for repo in (other,):
        (repo / HANDOFF).parent.mkdir(parents=True, exist_ok=True)
        (repo / HANDOFF).write_text(body, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "handoff")
        _git(repo, "push", "-q", "origin", "HEAD")
    _git(mine, "pull", "-q")
    (other / ARCHIVED).parent.mkdir(parents=True)
    _git(other, "mv", HANDOFF, ARCHIVED)
    _git(other, "commit", "-qm", "archive")
    _git(other, "push", "-q", "origin", "HEAD")
    # 這台自己也歸檔了同一份,還沒保存;Windows 的工作區是 CRLF
    if crlf:
        _git(mine, "config", "core.autocrlf", "true")
    (mine / ARCHIVED).parent.mkdir(parents=True)
    (mine / ARCHIVED).write_bytes(body.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))
    (mine / HANDOFF).unlink()
    _git(mine, "fetch", "-q")


@pytest.mark.parametrize("crlf", [False, True])
def test_the_same_archive_on_both_machines_is_no_deadlock(machines, crlf: bool) -> None:
    mine, other = machines
    _both_archive_the_same_handoff(mine, other, crlf=crlf)
    note = mine / "memory/topics/new.md"
    note.parent.mkdir(parents=True)
    note.write_text("a local note\n", encoding="utf-8")

    assert autopush._catch_up() is True

    assert _git(mine, "rev-list", "--count", "HEAD..@{upstream}") == "0"
    assert (mine / ARCHIVED).is_file() and not (mine / HANDOFF).exists()
    assert note.read_text(encoding="utf-8") == "a local note\n"


def test_a_different_local_copy_is_never_thrown_away(machines) -> None:
    mine, other = machines
    _both_archive_the_same_handoff(mine, other)
    (mine / ARCHIVED).write_text("## Goal\n本機改過\n", encoding="utf-8")

    from ai_config.identical_incoming import step_aside_identical

    assert step_aside_identical(mine, _git(mine, "rev-parse", "@{upstream}")).paths == []
    assert (mine / ARCHIVED).read_text(encoding="utf-8") == "## Goal\n本機改過\n"
    assert not (mine / HANDOFF).exists(), "合併還是會被擋,工作區一個字都不動"
    assert autopush._catch_up() is False



def _upstream(repo: Path) -> str:
    return _git(repo, "rev-parse", "@{upstream}")


def test_a_failed_merge_puts_every_stepped_aside_file_back(machines) -> None:
    from ai_config.identical_incoming import step_aside_identical

    mine, other = machines
    _both_archive_the_same_handoff(mine, other, crlf=True)
    before = (mine / ARCHIVED).read_bytes()

    aside = step_aside_identical(mine, _upstream(mine))
    assert sorted(aside.paths) == sorted([HANDOFF, ARCHIVED])
    aside.undo()

    assert (mine / ARCHIVED).read_bytes() == before, "連 CRLF 都放回原樣"
    assert not (mine / HANDOFF).exists()


def test_a_wildcard_in_a_remote_path_touches_only_that_file(machines) -> None:
    """git checkout reads a path as a pattern unless told it is literal."""
    from ai_config.identical_incoming import step_aside_identical

    mine, other = machines
    for name in ("memory/a*.md", "memory/ab.md"):
        (other / name).write_text("base\n", encoding="utf-8")
    _git(other, "add", ".")
    _git(other, "commit", "-qm", "files")
    _git(other, "push", "-q", "origin", "HEAD")
    _git(mine, "pull", "-q")
    (other / "memory/a*.md").unlink()
    _git(other, "commit", "-qam", "drop the starred one")
    _git(other, "push", "-q", "origin", "HEAD")
    (mine / "memory/a*.md").unlink()
    (mine / "memory/ab.md").write_text("my unsaved edit\n", encoding="utf-8")
    _git(mine, "fetch", "-q")

    step_aside_identical(mine, _upstream(mine))

    assert (mine / "memory/ab.md").read_text(encoding="utf-8") == "my unsaved edit\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable bit")
def test_a_different_executable_bit_is_not_the_same_file(machines) -> None:
    from ai_config.identical_incoming import step_aside_identical

    mine, other = machines
    _both_archive_the_same_handoff(mine, other)
    (mine / ARCHIVED).chmod(0o755)

    assert step_aside_identical(mine, _upstream(mine)).paths == []
    assert (mine / ARCHIVED).is_file()


def test_a_clean_filter_is_never_run_to_compare(machines, tmp_path: Path) -> None:
    """The path comes from the remote; comparing must not run a configured command."""
    from ai_config.identical_incoming import step_aside_identical

    mine, other = machines
    marker = tmp_path / "filter-ran"
    _git(mine, "config", "filter.spy.clean", f"touch {marker}; cat")
    (mine / ".git/info").mkdir(exist_ok=True)
    (mine / ".git/info/attributes").write_text("*.md filter=spy\n", encoding="utf-8")
    _both_archive_the_same_handoff(mine, other)
    _git(mine, "status", "--porcelain")  # 已追蹤檔案的 filter 是 git 自己的事,先讓它跑完
    marker.unlink(missing_ok=True)

    aside = step_aside_identical(mine, _upstream(mine))

    assert ARCHIVED in aside.paths
    assert not marker.exists()


def test_a_remote_path_is_printed_without_its_control_characters() -> None:
    from ai_config.identical_incoming import printable

    assert printable("memory/a\x1b[31mred.md") == "memory/a\\x1b[31mred.md"
    assert printable("memory/中文.md") == "memory/中文.md"
