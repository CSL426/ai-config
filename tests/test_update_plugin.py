"""`acg update` brings the Claude Code plugin along with the binary."""

import subprocess

import pytest

from ai_config.commands import update


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list:
    seen: list = []

    def run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "Plugin updated", "")

    monkeypatch.setattr(update.subprocess, "run", run)
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/bin/{name}")
    return seen


def test_a_successful_update_also_updates_the_plugin(
    calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The binary updated and the plugin did not: three machines sat on
    plugin 1.0.63 while acg itself was at 1.0.79, because nothing but a
    person remembering ever ran `claude plugin update`."""
    monkeypatch.setattr(update, "_run_update", lambda version=None: 0)

    assert update.run_update() == 0

    assert ["/bin/claude", "plugin", "update", "acg@acg"] in calls


def test_a_failed_update_leaves_the_plugin_alone(
    calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "_run_update", lambda version=None: 1)

    assert update.run_update() == 1

    assert calls == []


def test_a_plugin_that_will_not_update_does_not_fail_the_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update, "_run_update", lambda version=None: 0)
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/bin/{name}")

    def refuse(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "Plugin not installed")

    monkeypatch.setattr(update.subprocess, "run", refuse)

    assert update.run_update() == 0


def test_without_claude_there_is_nothing_to_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update, "_run_update", lambda version=None: 0)
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    monkeypatch.setattr(update, "_claude_binary", lambda: None)

    assert update.run_update() == 0
