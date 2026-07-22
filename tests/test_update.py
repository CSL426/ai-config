"""Behaviour tests for the self-update command."""

import sys
from pathlib import Path

from test_apply_projection import run_ai_config
from test_commands import make_full_repo

# INVARIANT: ai_config must not be imported at module scope — pytest imports
# every test module during collection, and ai_config.paths freezes ENTRYPOINT
# from the environment at first import, which breaks
# test_console_main_usage_entrypoint. Import inside each test instead.


def test_update_from_source_explains_and_fails(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)

    result = run_ai_config(repo_dir, home_dir, "update")

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "source" in combined
    assert "git pull" in combined


def test_update_rejects_extra_arguments(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)

    result = run_ai_config(repo_dir, home_dir, "update", "claude")

    assert result.returncode == 1


def test_update_frozen_runs_hosted_installer(monkeypatch) -> None:
    from ai_config import update

    calls = {}

    class Completed:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return Completed()

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    monkeypatch.setattr(update, "_current_version", lambda: "1.0.5")
    monkeypatch.setattr(update, "_latest_release_version", lambda: "1.0.6")
    monkeypatch.setattr(update, "NATIVE_WINDOWS", False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert update.run_update() == 0
    command_text = " ".join(calls["cmd"])
    assert "install.sh" in command_text
    assert "CSL426/ai-config" in command_text


def test_update_frozen_honours_repository_override(monkeypatch) -> None:
    from ai_config import update

    calls = {}

    class Completed:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return Completed()

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    monkeypatch.setattr(update, "_current_version", lambda: "1.0.5")
    monkeypatch.setattr(update, "_latest_release_version", lambda: "1.0.6")
    monkeypatch.setattr(update, "NATIVE_WINDOWS", False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("AI_CONFIG_TOOL_REPOSITORY", "someone/fork")

    assert update.run_update() == 0
    assert "someone/fork" in " ".join(calls["cmd"])


def test_update_frozen_native_windows_prints_manual_command(
    monkeypatch, capsys
) -> None:
    from ai_config import update

    def fail_run(cmd, **kwargs):
        raise AssertionError("update must not spawn a process on Windows")

    monkeypatch.setattr(update.subprocess, "run", fail_run)
    monkeypatch.setattr(update, "_current_version", lambda: "1.0.5")
    monkeypatch.setattr(update, "_latest_release_version", lambda: "1.0.6")
    monkeypatch.setattr(update, "NATIVE_WINDOWS", True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert update.run_update() == 1
    assert "install.ps1" in capsys.readouterr().out


def test_update_frozen_skips_download_when_current(monkeypatch, capsys) -> None:
    from ai_config import update

    def fail_run(cmd, **kwargs):
        raise AssertionError("current release must not download the installer")

    monkeypatch.setattr(update.subprocess, "run", fail_run)
    monkeypatch.setattr(update, "_current_version", lambda: "1.0.6")
    monkeypatch.setattr(update, "_latest_release_version", lambda: "1.0.6")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert update.run_update() == 0
    assert "already up to date" in capsys.readouterr().out


def test_update_frozen_does_not_downgrade_newer_version(monkeypatch) -> None:
    from ai_config import update

    def fail_run(cmd, **kwargs):
        raise AssertionError("newer release must not download the installer")

    monkeypatch.setattr(update.subprocess, "run", fail_run)
    monkeypatch.setattr(update, "_current_version", lambda: "1.1.0")
    monkeypatch.setattr(update, "_latest_release_version", lambda: "1.0.6")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert update.run_update() == 0
