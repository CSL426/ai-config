"""Project journal access survives migration without opting into Git sync."""

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_memory_safety import isolated_memory  # noqa: F401

from ai_config import (
    handoff_reminder,
    memory_hooks,
    memory_index,
    memory_journal,
    memory_paths,
    memory_plan,
    safety,
)
from ai_config.commands import memory_lifecycle


@pytest.fixture
def migrated(isolated_memory):  # noqa: F811
    _repo, home = isolated_memory
    root = home / "project"
    entry = root / ".remember"
    entry.mkdir(parents=True)
    target = memory_journal.journal_link(root)
    target.mkdir(parents=True)
    (target / "recent.md").write_text("original history\n")
    note = (
        f"Memory data migrated to:\n  {target}\n"
        "This directory is now empty; you may delete it.\n"
    )
    (entry / memory_journal.MIGRATED_NOTE).write_text(note)
    return root, entry, target


def test_migrated_local_journal_gets_idempotent_project_entry(migrated):
    root, entry, target = migrated
    assert memory_hooks.repair_entry(root)
    assert safety.is_reparse_point(entry)
    assert (entry / "recent.md").read_text() == "original history\n"
    assert memory_journal.journal_state(root)[0] == "local"
    assert not memory_journal.project_journal_dir(memory_paths.project_key(root)).exists()
    assert memory_hooks.repair_entry(root) is False
    assert (target / "recent.md").read_text() == "original history\n"
    assert not list(root.glob(".remember.acg-*"))


def test_notice_spelled_through_shared_memory_link_is_accepted(migrated):
    root, entry, target = migrated
    assert memory_paths.create_link()[0]
    spelled = memory_paths.MEMORY_LINK / memory_paths.JOURNAL_DIR_NAME / target.name
    assert str(spelled) != str(target)
    (entry / memory_journal.MIGRATED_NOTE).write_text(
        f"Memory data migrated to:\n  {spelled}\n"
        "This directory is now empty; you may delete it.\n"
    )
    assert memory_hooks.repair_entry(root)
    assert safety.is_reparse_point(entry)
    assert (entry / "recent.md").read_text() == "original history\n"
    assert memory_journal.journal_state(root)[0] == "local"


def test_directory_only_gitignore_pattern_still_gets_exclude(migrated):
    root, entry, _target = migrated
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / ".gitignore").write_text(".remember/\n")
    assert memory_hooks.repair_entry(root)
    assert safety.is_reparse_point(entry)
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", ".remember"], check=False
    )
    assert ignored.returncode == 0
    # Windows 的 junction 對 git 就是目錄,`.remember/` 直接命中;其他平台靠
    # exclude 接手。要的是同一個結果:git status 不列出入口
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ".remember" not in status.stdout


def test_notice_spelled_the_msys_way_is_accepted(migrated, monkeypatch):
    # Git Bash 下外掛寫的是 /c/Users/...;Windows 打不開這種寫法,正規化後
    # 會變成目前磁碟機上的 \c\Users\...,和原生路徑永遠不相等
    root, entry, target = migrated
    monkeypatch.setattr(memory_hooks, "WINDOWS_MODE", True)
    drive, _, rest = str(target).partition(":")
    if rest:  # Windows:C:\x -> /c/x,外掛在 Git Bash 下寫的就是這個形狀
        spelled = f"/{drive.lower()}{rest}".replace("\\", "/")
    else:  # POSIX:沒有磁碟機代號可翻,通知本來就是原生寫法
        spelled = str(target)
    (entry / memory_journal.MIGRATED_NOTE).write_text(
        f"Memory data migrated to:\n  {spelled}\n"
        "This directory is now empty; you may delete it.\n"
    )
    assert memory_hooks.repair_entry(root)
    assert safety.is_reparse_point(entry)
    assert (entry / "recent.md").read_text() == "original history\n"


def test_msys_translation_leaves_other_shapes_alone():
    translate = memory_hooks._from_msys_path
    assert translate("/c/Users/x") == r"c:\Users\x"
    for untouched in ("/home/human/ai-config", r"C:\Users\x", "/cd/Users/x", "/"):
        assert translate(untouched) is None


@pytest.mark.parametrize("conflict", ["content", "bad_notice"])
def test_repair_refuses_ambiguous_old_directory(migrated, conflict):
    root, entry, target = migrated
    if conflict == "content":
        (entry / "recent.md").write_text("unmoved history")
    else:
        (entry / memory_journal.MIGRATED_NOTE).write_text("not an acg migration")
    original = {p.name: p.read_bytes() for p in entry.iterdir()}
    with pytest.raises(RuntimeError):
        memory_hooks.repair_entry(root)
    assert {p.name: p.read_bytes() for p in entry.iterdir()} == original
    assert (target / "recent.md").read_text() == "original history\n"


def test_failed_link_restores_migration_notice(migrated, monkeypatch):
    root, entry, target = migrated
    original = (entry / memory_journal.MIGRATED_NOTE).read_bytes()

    def fail(*_args):
        raise OSError("injected junction failure")

    monkeypatch.setattr(memory_journal, "_create_journal_link", fail)
    with pytest.raises(OSError, match="injected"):
        memory_hooks.repair_entry(root)
    assert (entry / memory_journal.MIGRATED_NOTE).read_bytes() == original
    assert (target / "recent.md").read_text() == "original history\n"
    assert not list(root.glob(".remember.acg-*"))


def test_exclude_write_failure_restores_old_directory(migrated, monkeypatch):
    root, entry, _target = migrated
    exclude = root / "exclude"
    monkeypatch.setattr(memory_journal, "project_git_exclude", lambda _: (exclude, "new"))

    def fail(*_args):
        raise OSError("injected exclude failure")

    monkeypatch.setattr(memory_paths, "_write_text_atomic", fail)
    with pytest.raises(OSError, match="exclude"):
        memory_hooks.repair_entry(root)
    assert not safety.is_reparse_point(entry)
    assert (entry / memory_journal.MIGRATED_NOTE).is_file()


def test_hook_repairs_only_while_memory_enabled(migrated, monkeypatch, capsys):
    root, entry, _target = migrated
    memory_index.ensure_index()
    assert memory_paths.create_link()[0]
    memory_paths.install_block(memory_paths.live_rules_path())
    memory_journal.install_journal_config()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root)})))
    assert memory_hooks.run([str(memory_paths.SCRIPT_DIR)]) == 0
    assert safety.is_reparse_point(entry)
    assert capsys.readouterr().out == ""
    memory_journal._remove_journal_link(entry, Path(memory_journal.journal_state(root)[1]))
    memory_journal.remove_journal_config()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root)})))
    assert memory_hooks.run([str(memory_paths.SCRIPT_DIR)]) == 0
    assert not entry.exists()


def test_hook_does_not_create_journal_before_plugin(isolated_memory):  # noqa: F811
    _repo, home = isolated_memory
    root = home / "new-project"
    root.mkdir()
    assert memory_hooks.repair_entry(root) is False
    assert list(root.iterdir()) == []
    assert not memory_journal.journal_link(root).exists()
    assert memory_hooks.repair_entry(home) is False


def test_hook_rejects_foreign_entry(migrated):
    root, entry, _target = migrated
    (entry / memory_journal.MIGRATED_NOTE).unlink()
    entry.rmdir()
    outside = root / "outside"
    outside.mkdir()
    memory_journal._create_journal_link(outside, entry)
    with pytest.raises(RuntimeError, match="其他位置"):
        memory_hooks.repair_entry(root)
    assert entry.resolve() == outside.resolve()


def test_enable_preview_lists_hooks_and_disable_preserves_others(
    isolated_memory, monkeypatch,  # noqa: F811
):
    monkeypatch.setattr(memory_journal, "remember_installed", lambda: True)
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
    assert memory_lifecycle.execute("enable").code == 0
    installed = json.loads(path.read_text())
    assert handoff_reminder.without_settings(
        memory_hooks.without_hooks(installed)
    ) == original
    assert installed["model"] == "local-model"
    memory_hooks.install(enabling=True)
    assert json.loads(path.read_text()) == installed
    assert memory_lifecycle.execute("disable").code == 0
    assert json.loads(path.read_text()) == original


def test_memory_enable_turns_the_handoff_reminder_on_and_disable_off(
    isolated_memory,  # noqa: F811
):
    path = memory_hooks.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"model": "local-model"}))

    reasons = [c["reason"] for c in memory_plan.plan("enable").changes
               if c["destination"] == str(path)]
    assert reasons and "開啟交接提醒(門檻 70%)" in reasons[0]
    assert memory_lifecycle.execute("enable").code == 0
    state = handoff_reminder.status()
    assert state == {"enabled": True, "threshold": 70, "installed": True}

    reasons = [c["reason"] for c in memory_plan.plan("disable").changes
               if c["destination"] == str(path)]
    assert reasons and "移除交接提醒" in reasons[0]
    assert memory_lifecycle.execute("disable").code == 0
    assert handoff_reminder.status()["enabled"] is False
    assert json.loads(path.read_text()) == {"model": "local-model"}


def test_memory_enable_keeps_a_chosen_reminder_threshold(isolated_memory):  # noqa: F811
    memory_paths.CLAUDE_HOME.mkdir(parents=True)
    handoff_reminder.configure(True, 85)
    line = json.loads(memory_hooks.settings_path().read_text())["statusLine"]

    assert memory_lifecycle.execute("enable").code == 0

    assert handoff_reminder.status()["threshold"] == 85
    assert json.loads(memory_hooks.settings_path().read_text())["statusLine"] == line


def test_a_broken_reminder_does_not_block_memory_enable(
    isolated_memory, monkeypatch,  # noqa: F811
):
    def refuse(*_args, **_kwargs):
        raise ValueError("無法辨識原本的 status line")

    monkeypatch.setattr(handoff_reminder, "follow_memory", refuse)

    assert memory_lifecycle.execute("enable").code == 0
    assert memory_paths.MEMORY_LINK.exists()


def test_sync_filters_source_hooks_and_keeps_local_hooks(isolated_memory):  # noqa: F811
    from ai_config.tools.claude import (
        filter_claude_settings,
        merge_claude_settings,
        shared_claude_settings,
    )

    memory_hooks.install(enabling=True)
    local = json.loads(memory_hooks.settings_path().read_text())
    source = json.loads(json.dumps(local).replace(str(memory_paths.SCRIPT_DIR), "/other/data"))
    source["hooks"]["SessionStart"].append({
        "hooks": [{"type": "command", "command": "shared-hook"}],
    })
    text = json.dumps(source)
    filtered = json.loads(filter_claude_settings(text))
    assert str(memory_paths.SCRIPT_DIR) not in json.dumps(filtered)
    assert "/other/data" not in json.dumps(filtered)
    assert filtered == shared_claude_settings(text)
    merged = json.loads(merge_claude_settings(text, json.dumps(local)))
    assert memory_hooks.without_hooks(merged) == filtered
    assert memory_hooks.preserve_hooks(filtered, local) == merged
    assert json.loads(merge_claude_settings(text, "{}")) == filtered


def test_registered_exec_hook_runs_without_shell(migrated):
    root, entry, _target = migrated
    (memory_paths.SCRIPT_DIR / "claude").mkdir()
    memory_index.ensure_index()
    assert memory_paths.create_link()[0]
    memory_paths.install_block(memory_paths.live_rules_path())
    memory_journal.install_journal_config()
    memory_hooks.install(enabling=True)
    settings = json.loads(memory_hooks.settings_path().read_text())
    hook = settings["hooks"]["SessionStart"][0]["hooks"][0]
    result = subprocess.run(
        [hook["command"], *hook["args"]],
        input=json.dumps({"cwd": str(root)}),
        text=True, capture_output=True, check=False,
        env={
            **os.environ,
            "HOME": str(memory_paths.HOME), "USERPROFILE": str(memory_paths.HOME),
            "AI_CONFIG_REPO": str(memory_paths.SCRIPT_DIR),
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert safety.is_reparse_point(entry)
    assert (entry / "recent.md").read_text() == "original history\n"


def test_a_new_session_hears_about_a_failed_autopush(migrated, monkeypatch, capsys, tmp_path):
    from ai_config import autopush

    root, _entry, _target = migrated
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert autopush.push_and_record(lambda: print("✗ push cancelled") or 1) == 1
    capsys.readouterr()

    for event, expected in (("UserPromptSubmit", False), ("SessionStart", True)):
        payload = {"cwd": str(root), "hook_event_name": event}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        memory_hooks.run([str(memory_paths.SCRIPT_DIR)])
        out = capsys.readouterr().out
        # 每次送出提示都重複會變成噪音,只在開會話時說一次
        assert ("自動上傳失敗" in out) is expected
        if expected:
            assert "push cancelled" in out
