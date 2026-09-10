"""Project journal access survives migration without opting into Git sync."""

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_memory_safety import isolated_memory  # noqa: F401

from ai_config import memory, memory_hooks, memory_plan
from ai_config.commands import memory as command


@pytest.fixture
def migrated(isolated_memory):  # noqa: F811
    _repo, home = isolated_memory
    root = home / "project"
    entry = root / ".remember"
    entry.mkdir(parents=True)
    target = memory.journal_link(root)
    target.mkdir(parents=True)
    (target / "recent.md").write_text("original history\n")
    note = (
        f"Memory data migrated to:\n  {target}\n"
        "This directory is now empty; you may delete it.\n"
    )
    (entry / memory.MIGRATED_NOTE).write_text(note)
    return root, entry, target


def test_migrated_local_journal_gets_idempotent_project_entry(migrated):
    root, entry, target = migrated
    assert memory_hooks.repair_entry(root)
    assert memory.is_reparse_point(entry)
    assert (entry / "recent.md").read_text() == "original history\n"
    assert memory.journal_state(root)[0] == "local"
    assert not memory.project_journal_dir(memory.project_key(root)).exists()
    assert memory_hooks.repair_entry(root) is False
    assert (target / "recent.md").read_text() == "original history\n"
    assert not list(root.glob(".remember.acg-*"))


def test_notice_spelled_through_shared_memory_link_is_accepted(migrated):
    root, entry, target = migrated
    assert memory.create_link()[0]
    spelled = memory.MEMORY_LINK / memory.JOURNAL_DIR_NAME / target.name
    assert str(spelled) != str(target)
    (entry / memory.MIGRATED_NOTE).write_text(
        f"Memory data migrated to:\n  {spelled}\n"
        "This directory is now empty; you may delete it.\n"
    )
    assert memory_hooks.repair_entry(root)
    assert memory.is_reparse_point(entry)
    assert (entry / "recent.md").read_text() == "original history\n"
    assert memory.journal_state(root)[0] == "local"


def test_directory_only_gitignore_pattern_still_gets_exclude(migrated):
    root, entry, _target = migrated
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / ".gitignore").write_text(".remember/\n")
    assert memory_hooks.repair_entry(root)
    assert memory.is_reparse_point(entry)
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", ".remember"], check=False
    )
    assert ignored.returncode == 0
    exclude = root / ".git/info/exclude"
    assert ".remember" in exclude.read_text().splitlines()


@pytest.mark.parametrize("conflict", ["content", "bad_notice"])
def test_repair_refuses_ambiguous_old_directory(migrated, conflict):
    root, entry, target = migrated
    if conflict == "content":
        (entry / "recent.md").write_text("unmoved history")
    else:
        (entry / memory.MIGRATED_NOTE).write_text("not an acg migration")
    original = {p.name: p.read_bytes() for p in entry.iterdir()}
    with pytest.raises(RuntimeError):
        memory_hooks.repair_entry(root)
    assert {p.name: p.read_bytes() for p in entry.iterdir()} == original
    assert (target / "recent.md").read_text() == "original history\n"


def test_failed_link_restores_migration_notice(migrated, monkeypatch):
    root, entry, target = migrated
    original = (entry / memory.MIGRATED_NOTE).read_bytes()

    def fail(*_args):
        raise OSError("injected junction failure")

    monkeypatch.setattr(memory, "_create_journal_link", fail)
    with pytest.raises(OSError, match="injected"):
        memory_hooks.repair_entry(root)
    assert (entry / memory.MIGRATED_NOTE).read_bytes() == original
    assert (target / "recent.md").read_text() == "original history\n"
    assert not list(root.glob(".remember.acg-*"))


def test_exclude_write_failure_restores_old_directory(migrated, monkeypatch):
    root, entry, _target = migrated
    exclude = root / "exclude"
    monkeypatch.setattr(memory, "project_git_exclude", lambda _: (exclude, "new"))

    def fail(*_args):
        raise OSError("injected exclude failure")

    monkeypatch.setattr(memory, "_write_text_atomic", fail)
    with pytest.raises(OSError, match="exclude"):
        memory_hooks.repair_entry(root)
    assert not memory.is_reparse_point(entry)
    assert (entry / memory.MIGRATED_NOTE).is_file()


def test_hook_repairs_only_while_memory_enabled(migrated, monkeypatch, capsys):
    root, entry, _target = migrated
    memory.ensure_index()
    assert memory.create_link()[0]
    memory.install_block(memory.live_rules_path())
    memory.install_journal_config()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root)})))
    assert memory_hooks.run([str(memory.SCRIPT_DIR)]) == 0
    assert memory.is_reparse_point(entry)
    assert capsys.readouterr().out == ""
    memory._remove_journal_link(entry, Path(memory.journal_state(root)[1]))
    memory.remove_journal_config()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root)})))
    assert memory_hooks.run([str(memory.SCRIPT_DIR)]) == 0
    assert not entry.exists()


def test_hook_does_not_create_journal_before_plugin(isolated_memory):  # noqa: F811
    _repo, home = isolated_memory
    root = home / "new-project"
    root.mkdir()
    assert memory_hooks.repair_entry(root) is False
    assert list(root.iterdir()) == []
    assert not memory.journal_link(root).exists()
    assert memory_hooks.repair_entry(home) is False


def test_hook_rejects_foreign_entry(migrated):
    root, entry, _target = migrated
    (entry / memory.MIGRATED_NOTE).unlink()
    entry.rmdir()
    outside = root / "outside"
    outside.mkdir()
    memory._create_journal_link(outside, entry)
    with pytest.raises(RuntimeError, match="其他位置"):
        memory_hooks.repair_entry(root)
    assert entry.resolve() == outside.resolve()


def test_enable_preview_lists_hooks_and_disable_preserves_others(
    isolated_memory, monkeypatch,  # noqa: F811
):
    monkeypatch.setattr(memory, "remember_installed", lambda: True)
    path = memory_hooks.settings_path()
    path.parent.mkdir(parents=True)
    original = {"hooks": {"SessionStart": [{
        "hooks": [{"type": "command", "command": "existing-hook"}],
    }]}, "model": "local-model"}
    path.write_text(json.dumps(original))
    before = path.read_bytes()
    plan = memory_plan.plan("enable")
    assert path.read_bytes() == before
    assert any(c["destination"] == str(path) for c in plan.changes)
    assert command.execute("enable").code == 0
    installed = json.loads(path.read_text())
    assert memory_hooks.without_hooks(installed) == original
    assert installed["model"] == "local-model"
    memory_hooks.install(enabling=True)
    assert json.loads(path.read_text()) == installed
    assert command.execute("disable").code == 0
    assert json.loads(path.read_text()) == original


def test_sync_filters_source_hooks_and_keeps_local_hooks(isolated_memory):  # noqa: F811
    from ai_config.tools.claude import (
        filter_claude_settings,
        merge_claude_settings,
        shared_claude_settings,
    )

    memory_hooks.install(enabling=True)
    local = json.loads(memory_hooks.settings_path().read_text())
    source = json.loads(json.dumps(local).replace(str(memory.SCRIPT_DIR), "/other/data"))
    source["hooks"]["SessionStart"].append({
        "hooks": [{"type": "command", "command": "shared-hook"}],
    })
    text = json.dumps(source)
    filtered = json.loads(filter_claude_settings(text))
    assert str(memory.SCRIPT_DIR) not in json.dumps(filtered)
    assert "/other/data" not in json.dumps(filtered)
    assert filtered == shared_claude_settings(text)
    merged = json.loads(merge_claude_settings(text, json.dumps(local)))
    assert memory_hooks.without_hooks(merged) == filtered
    assert memory_hooks.preserve_hooks(filtered, local) == merged
    assert json.loads(merge_claude_settings(text, "{}")) == filtered


def test_registered_exec_hook_runs_without_shell(migrated):
    root, entry, _target = migrated
    (memory.SCRIPT_DIR / "claude").mkdir()
    memory.ensure_index()
    assert memory.create_link()[0]
    memory.install_block(memory.live_rules_path())
    memory.install_journal_config()
    memory_hooks.install(enabling=True)
    settings = json.loads(memory_hooks.settings_path().read_text())
    hook = settings["hooks"]["SessionStart"][0]["hooks"][0]
    result = subprocess.run(
        [hook["command"], *hook["args"]],
        input=json.dumps({"cwd": str(root)}),
        text=True, capture_output=True, check=False,
        env={
            **os.environ,
            "HOME": str(memory.HOME), "USERPROFILE": str(memory.HOME),
            "AI_CONFIG_REPO": str(memory.SCRIPT_DIR),
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert memory.is_reparse_point(entry)
    assert (entry / "recent.md").read_text() == "original history\n"
