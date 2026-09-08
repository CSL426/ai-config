"""Journal collision and interrupted migration regression coverage."""

from pathlib import Path

import pytest
from test_memory_safety import isolated_memory, symlink  # noqa: F401

from ai_config import memory


@pytest.fixture
def project(isolated_memory, monkeypatch):  # noqa: F811 - imported pytest fixture
    _repo, home = isolated_memory
    root = home / "project"
    root.mkdir()
    key = memory.ProjectKey("team--app", True, "remote")
    monkeypatch.setattr(memory, "project_key", lambda _cwd=None: key)
    return root


def test_local_adopt_release_adopt_preserves_metadata_and_notes(project):
    local = memory.journal_link(project)
    local.mkdir(parents=True)
    (local / ".gitignore").write_text("*\n")
    (local / memory.MIGRATED_NOTE).write_text("migration metadata")
    (local / "recent.md").write_text("history")
    memory.adopt_journal(project)
    target = memory.project_journal_dir(memory.project_key(project))
    assert local.is_dir()
    assert (target / "recent.md").read_text() == "history"
    assert (target / memory.MIGRATED_NOTE).read_text() == "migration metadata"
    assert any(p.read_text() == "*\n" for p in target.glob(".gitignore.from-*"))
    memory.release_journal(project)
    assert not local.is_symlink()
    memory.adopt_journal(project)
    assert (target / "recent.md").read_text() == "history"


def test_repeated_collision_preserves_every_copy(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "recent.md").write_text("original")
    for content in ("first", "second", "third"):
        (source / "recent.md").write_text(content)
        memory._move_contents(source, target)
    assert {p.read_text() for p in target.iterdir()} == {
        "original", "first", "second", "third"
    }


def test_link_failure_restores_local_contents(project, monkeypatch):
    local = memory.journal_link(project)
    local.mkdir(parents=True)
    originals = {".gitignore": "*\n", "recent.md": "history"}
    for name, content in originals.items():
        (local / name).write_text(content)

    def fail(_target, _link):
        raise OSError("injected link failure")

    monkeypatch.setattr(memory, "_create_journal_link", fail)
    with pytest.raises(OSError, match="injected"):
        memory.adopt_journal(project)
    assert not local.is_symlink()
    assert {p.name: p.read_text() for p in local.iterdir()} == originals


def test_move_failure_restores_already_moved_content(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    for name in ("a.md", "b.md"):
        (source / name).write_text(name)
    original_move = memory.shutil.move

    def fail_second(src, dst):
        if Path(src).name == "b.md":
            raise OSError("injected move failure")
        return original_move(src, dst)

    monkeypatch.setattr(memory.shutil, "move", fail_second)
    with pytest.raises(OSError, match="injected"):
        memory._move_contents(source, target)
    assert {p.name: p.read_text() for p in source.iterdir()} == {
        "a.md": "a.md", "b.md": "b.md"
    }
    assert not list(target.iterdir())


def test_adopt_refuses_nested_symlink_before_moving(project, tmp_path):
    local = memory.journal_link(project)
    local.mkdir(parents=True)
    (local / "recent.md").write_text("history")
    external = tmp_path / "external.md"
    external.write_text("outside")
    symlink(local / "outside.md", external)
    with pytest.raises(RuntimeError, match="reparse"):
        memory.adopt_journal(project)
    assert (local / "recent.md").read_text() == "history"
    assert external.read_text() == "outside"
