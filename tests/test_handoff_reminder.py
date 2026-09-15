"""Reminder contracts: portable settings, isolated sessions, no handoff writes."""

import io
import json
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_config import cli, locking, memory
from ai_config import handoff_reminder as remind
from ai_config.commands import memory as command
from ai_config.tools.claude import (
    filter_claude_settings,
    merge_claude_settings,
    shared_claude_settings,
)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    home.mkdir()
    backup = tmp_path / "backup"
    monkeypatch.setattr(memory, "CLAUDE_HOME", home)
    monkeypatch.setattr(locking, "BACKUP_BASE", backup)
    monkeypatch.setattr(command, "BACKUP_BASE", backup)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    path = home / "settings.json"
    path.write_text(json.dumps({
        "statusLine": {"type": "command", "command": "printf original", "padding": 2},
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}]},
    }))
    return path


def payload(used=75, session="first", event="PostToolUse"):
    return {
        "session_id": session, "cwd": str(Path.cwd()),
        "hook_event_name": event, "context_window": {"used_percentage": used},
    }


def test_install_restore_and_back_up_without_touching_other_hooks(settings):
    original = json.loads(settings.read_text())
    assert remind.configure(True) == {"enabled": True, "installed": True, "threshold": 70}
    installed = settings.read_bytes()
    remind.configure(True)
    assert settings.read_bytes() == installed
    assert remind.without_settings(json.loads(installed)) == original
    remind.configure(False)
    assert json.loads(settings.read_text()) == original
    assert len(list(command.BACKUP_BASE.glob("memory-*/manifest.json"))) == 2


def test_enable_without_display_and_disable_restores_absence(settings):
    settings.write_text("{}")
    remind.configure(True)
    assert remind.status()["installed"]
    remind.configure(False)
    assert json.loads(settings.read_text()) == {}


def test_failed_install_preserves_settings_and_backup(settings, monkeypatch):
    original = settings.read_bytes()
    def fail(*_args):
        raise OSError("write failed")
    monkeypatch.setattr(memory, "_write_text_atomic", fail)
    with pytest.raises(OSError, match="write failed"):
        remind.configure(True)
    assert settings.read_bytes() == original
    assert len(list(command.BACKUP_BASE.glob("memory-*/manifest.json"))) == 1


def test_incomplete_hooks_report_and_repair(settings):
    remind.configure(True)
    document = json.loads(settings.read_text())
    document["hooks"].pop("PostToolUse")
    settings.write_text(json.dumps(document))
    assert remind.status() == {"enabled": True, "installed": False, "threshold": 70}
    remind.configure(True)
    assert remind.status()["installed"]


def test_shared_display_changes_survive_apply_and_disable(settings):
    remind.configure(True, 80)
    live = settings.read_text()
    portable = json.loads(filter_claude_settings(live))
    assert remind.STATUS_COMMAND not in json.dumps(portable)
    assert remind.MARKER not in json.dumps(portable)
    portable["statusLine"]["command"] = "printf changed"
    portable["statusLine"]["padding"] = 4
    merged = merge_claude_settings(json.dumps(portable), live)
    assert shared_claude_settings(merged) == portable
    settings.write_text(merged)
    assert remind.status()["threshold"] == 80
    remind.configure(False)
    assert json.loads(settings.read_text()) == portable


def test_reminder_once_across_hooks_and_transient_usage_then_compaction(settings):
    remind.configure(True)
    remind.record(payload(), 70)
    output = remind.reminder(payload())
    assert "75%" in output["hookSpecificOutput"]["additionalContext"].replace("％", "%")
    for used in (80, None, 65, 90):
        remind.record(payload(used), 70)
        assert remind.reminder(payload(event="UserPromptSubmit")) is None
    assert remind.reminder(payload(event="PreCompact")) is None
    assert remind.reminder(payload()) is None
    remind.record(payload(73), 70)
    assert remind.reminder(payload()) is not None
    assert not (memory.CLAUDE_HOME / "shared-memory").exists()


@pytest.mark.parametrize("used", [None, True, -1, 101, "75", float("nan"), float("inf")])
def test_invalid_usage_is_silent(settings, used):
    remind.configure(True)
    remind.record(payload(used), 70)
    assert remind.reminder(payload()) is None


def test_remaining_fallback_only_when_used_field_absent():
    value = payload()
    value["context_window"] = {"remaining_percentage": 20}
    assert remind._percentage(value) == 80
    value["context_window"]["used_percentage"] = None
    assert remind._percentage(value) is None


def test_windows_without_bash_quotes_executable_and_uses_powershell(settings, monkeypatch):
    monkeypatch.setattr(remind, "NATIVE_WINDOWS", True)
    monkeypatch.setattr(remind, "_bash", lambda: None)
    monkeypatch.setattr(sys, "executable", "C:/Program Files/acg/acg.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    remind.configure(True)
    document = json.loads(settings.read_text())
    assert document["statusLine"]["command"].startswith("& 'C:/Program Files/acg/acg.exe'")
    assert remind.status()["installed"]
    assert remind._shell_argv("Write-Output test")[-3:] == ["-NoProfile", "-Command", "Write-Output test"]
    assert remind.without_settings(document)["statusLine"]["command"] == "printf original"


def test_session_isolation_staleness_and_concurrent_delivery(settings, monkeypatch):
    remind.configure(True)
    remind.record(payload(), 70)
    assert remind.reminder(payload(session="second")) is None
    changed_cwd = {**payload(), "cwd": str(settings.parent)}
    assert remind.reminder(changed_cwd) is None
    now = remind.time.time()
    monkeypatch.setattr(remind.time, "time", lambda: now + remind.MAX_AGE + 1)
    assert remind.reminder(payload()) is None
    monkeypatch.setattr(remind.time, "time", lambda: now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _: remind.reminder(payload()), range(2)))
    assert sum(value is not None for value in outputs) == 1
    remind.reminder(payload(event="SessionEnd"))
    assert not remind._session_path(payload()).exists()


def test_subagent_cannot_consume_or_reset_parent_reminder(settings):
    remind.configure(True)
    remind.record(payload(), 70)
    for event in ("PostToolUse", "PreCompact", "SessionEnd"):
        assert remind.reminder({**payload(event=event), "agent_id": "child"}) is None
    assert remind.reminder(payload()) is not None


def test_wrapper_forwards_payload_and_bash_output_even_when_cache_fails(settings, monkeypatch, capsys):
    original = {"type": "command", "command": "items=(preserved); printf '%s:' \"${items[0]}\"; cat"}
    raw = json.dumps(payload(12))
    def fail(*_args):
        raise OSError("cache unavailable")
    monkeypatch.setattr(remind, "record", fail)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    assert remind.run_statusline([remind._encode(original), "70"]) == 0
    assert capsys.readouterr().out == "preserved:" + raw


def test_hook_fail_open_for_corrupt_payload_or_unsafe_cache(settings, monkeypatch, capsys):
    remind.configure(True)
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert remind.run_hook([]) == 0
    path = remind._session_path(payload())
    path.parent.mkdir()
    external = settings.parent / "external"
    external.write_text("unchanged")
    try:
        path.symlink_to(external)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload())))
    assert remind.run_hook([]) == 0
    assert external.read_text() == "unchanged"
    assert capsys.readouterr().out == ""


def test_cli_thresholds_and_installed_command_shape(settings):
    assert command.run_memory(["handoff", "remind", "enable", "81"]) == 0
    assert remind.status()["threshold"] == 81
    installed = settings.read_bytes()
    for value in ("0", "100", "bad", "7.5"):
        assert command.run_memory(["handoff", "remind", "enable", value]) == 1
        assert settings.read_bytes() == installed
    args = shlex.split(json.loads(installed)["statusLine"]["command"])
    assert args[1:4] == ["-m", "ai_config", remind.STATUS_COMMAND]


@pytest.mark.parametrize("hidden", [remind.STATUS_COMMAND, remind.HOOK_COMMAND])
def test_standalone_hook_never_pauses(hidden, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["acg.exe", hidden])
    monkeypatch.setattr(cli, "console_main", lambda: 0)
    def fail():
        raise AssertionError("must bypass interactive startup")
    monkeypatch.setattr(cli, "launched_by_double_click", fail)
    assert cli.standalone_main() == 0
