"""/acg follows acg: installed where missing, updated where present."""

import json
import subprocess
from pathlib import Path

import pytest

from ai_config import claude_plugin
from ai_config.commands import update

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake(monkeypatch: pytest.MonkeyPatch, plugins, markets, *, failing=()) -> list:
    """A claude CLI whose `plugin list --json` reports `plugins` (None = no --json)."""
    seen: list = []

    def run(argv, **kwargs):
        args = list(argv[1:])
        seen.append(args)
        if args[-1:] == ["--json"]:
            listing = markets if args[:3] == ["plugin", "marketplace", "list"] else plugins
            if listing is None:
                return subprocess.CompletedProcess(argv, 1, "", "unknown option --json")
            return subprocess.CompletedProcess(argv, 0, json.dumps(listing), "")
        code = 1 if args[1] in failing else 0
        return subprocess.CompletedProcess(argv, code, "done", "refused" if code else "")

    monkeypatch.setattr(claude_plugin.subprocess, "run", run)
    monkeypatch.setattr(claude_plugin, "claude_binary", lambda: "/bin/claude")
    monkeypatch.delenv(claude_plugin.OPT_OUT, raising=False)
    return seen


def _mutations(seen: list) -> list:
    return [args for args in seen if args[-1:] != ["--json"]]


USER_ACG = [{"id": "acg@acg", "scope": "user", "version": "1.0.96"}]


def test_an_installed_plugin_is_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, USER_ACG, [{"name": "acg"}])

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [["plugin", "update", "acg@acg"]]


def test_a_missing_plugin_is_installed_with_its_marketplace(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, [], [])

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [
        ["plugin", "marketplace", "add", "CSL426/ai-config"],
        ["plugin", "install", "acg@acg", "--scope", "user"],
    ]


def test_a_known_marketplace_is_not_added_again(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, [], [{"name": "acg"}])

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [["plugin", "install", "acg@acg", "--scope", "user"]]


def test_a_project_scoped_copy_does_not_count_as_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    # 某個專案裝了一份,不代表這個使用者在其他地方也有 /acg
    seen = _fake(monkeypatch, [{"id": "acg@acg", "scope": "project"}], [{"name": "acg"}])

    claude_plugin.ensure("CSL426/ai-config")

    assert ["plugin", "install", "acg@acg", "--scope", "user"] in _mutations(seen)


def test_a_claude_without_json_listing_is_only_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, None, None)

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [["plugin", "update", "acg@acg"]]


def test_opting_out_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, [], [])
    monkeypatch.setenv(claude_plugin.OPT_OUT, "1")

    claude_plugin.ensure("CSL426/ai-config")

    assert seen == []


def test_without_claude_there_is_nothing_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, [], [])
    monkeypatch.setattr(claude_plugin, "claude_binary", lambda: None)

    claude_plugin.ensure("CSL426/ai-config")

    assert seen == []


def test_a_refused_install_warns_and_the_update_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fake(monkeypatch, [], [{"name": "acg"}], failing=("install",))
    monkeypatch.setattr(update, "_run_update", lambda version=None: 0)
    monkeypatch.setattr(update, "_refresh_after_update", lambda: None)

    assert update.run_update() == 0
    assert "沒有裝上 /acg" in capsys.readouterr().out


def test_a_successful_update_brings_the_plugin_along(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three machines sat on plugin 1.0.63 while acg itself was at 1.0.79,
    because nothing but a person remembering ever ran `claude plugin update`."""
    seen = _fake(monkeypatch, USER_ACG, [{"name": "acg"}])
    monkeypatch.setattr(update, "_run_update", lambda version=None: 0)
    monkeypatch.setattr(update, "_refresh_after_update", lambda: None)

    assert update.run_update() == 0
    assert ["plugin", "update", "acg@acg"] in seen


def test_a_failed_update_leaves_the_plugin_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake(monkeypatch, [], [])
    monkeypatch.setattr(update, "_run_update", lambda version=None: 1)

    assert update.run_update() == 1
    assert seen == []


@pytest.mark.parametrize("script", ["install.sh", "install.ps1"])
def test_a_first_install_brings_the_plugin_and_can_be_told_not_to(script: str) -> None:
    text = (REPO_ROOT / script).read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if "Installation" in line and "NO_PLUGIN" in line)

    assert "AI_CONFIG_NO_PLUGIN" in line
    assert "__claude-plugin" in text
