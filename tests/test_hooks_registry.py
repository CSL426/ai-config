"""One registry strips and restores every acg hook, whatever registers next."""

import json
from pathlib import Path

import pytest

from ai_config import hooks
from ai_config.commands.hooks import run_hooks
from ai_config.tools.claude import filter_claude_settings, merge_claude_settings


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude = tmp_path / ".claude"
    claude.mkdir()
    monkeypatch.setattr(hooks, "CLAUDE_HOME", claude)
    monkeypatch.setattr(hooks.memory, "CLAUDE_HOME", claude)
    return claude


def _settings(home: Path) -> dict:
    return json.loads((home / "settings.json").read_text(encoding="utf-8"))


def test_every_registered_hook_is_stripped_by_one_pass(home: Path) -> None:
    for hook in hooks.REGISTRY.values():
        hooks.configure(hook, True)
    live = (home / "settings.json").read_text(encoding="utf-8")
    assert len(hooks.REGISTRY) >= 3
    for hook in hooks.REGISTRY.values():
        assert hook.marker in live

    gathered = json.loads(filter_claude_settings(live))

    assert gathered.get("hooks", {}) == {}


def test_a_hook_registered_later_needs_no_change_to_the_sync(home: Path) -> None:
    """The point of the table: a fourth hook is an entry, not a fourth layer."""
    later = hooks.Hook(
        name="pretend-future", marker=f"{hooks.PREFIX}未來的檢查",
        events=("PostToolUse",), command="__pretend", summary="x",
    )
    hooks.REGISTRY[later.name] = later
    try:
        hooks.configure(later, True)
        live = (home / "settings.json").read_text(encoding="utf-8")
        assert later.marker in live

        assert json.loads(filter_claude_settings(live)).get("hooks", {}) == {}
    finally:
        hooks.REGISTRY.pop(later.name)


def test_apply_puts_this_machines_hooks_back(home: Path) -> None:
    hooks.configure(hooks.COMMIT_STYLE, True)
    live = (home / "settings.json").read_text(encoding="utf-8")
    incoming = json.dumps({"statusLine": {"type": "command", "command": "shared"}})

    merged = json.loads(merge_claude_settings(incoming, live))

    assert merged["statusLine"]["command"] == "shared"
    markers = {
        h["statusMessage"]
        for rows in merged["hooks"].values() for r in rows for h in r["hooks"]
    }
    assert hooks.COMMIT_STYLE.marker in markers


def test_another_tools_hook_is_never_touched(home: Path) -> None:
    (home / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "theirs"}]},
    ]}}), encoding="utf-8")

    hooks.configure(hooks.COMMIT_STYLE, True)
    hooks.configure(hooks.COMMIT_STYLE, False)

    assert _settings(home)["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "theirs"}]}
    ]


def test_the_command_lists_and_toggles(
    home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_hooks(["list"]) == 0
    assert "commit-style" in capsys.readouterr().out
    assert run_hooks(["enable", "commit-style"]) == 0
    assert hooks.installed(hooks.read_settings(), hooks.COMMIT_STYLE)
    assert run_hooks(["disable", "commit-style"]) == 0
    assert not hooks.installed(hooks.read_settings(), hooks.COMMIT_STYLE)


def test_an_unknown_name_is_refused(home: Path) -> None:
    assert run_hooks(["enable", "nope"]) == 1
    assert run_hooks(["bogus"]) == 1
