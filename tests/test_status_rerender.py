"""Status sees what apply would still change when acg, not the repo, moved on.

A machine applied commit X with one acg version, then updated acg. Same
commit, but the new version writes some files differently; status must
not call that already applied.
"""

import json
from pathlib import Path

import pytest

from ai_config import paths, shared_hooks
from ai_config.commands import status as status_cmd
from ai_config.tools import claude


def _settings(path: str) -> str:
    return json.dumps({
        "enabledPlugins": {"acg-console@acg-mods": True},
        "extraKnownMarketplaces": {
            "acg-mods": {"source": {"source": "directory", "path": path}},
        },
    }, indent=2) + "\n"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    repo = home / ".acg-data"
    (repo / "claude" / "mods").mkdir(parents=True)
    monkeypatch.setattr(paths, "HOME", home)
    monkeypatch.setattr(paths, "SCRIPT_DIR", repo)
    monkeypatch.setattr(paths, "scheduled_command", lambda: ["/opt/acg/ai-config"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(shared_hooks, "_windows", lambda: False)
    monkeypatch.setattr(shared_hooks, "_definition_paths", list)
    return home


def _status_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo_text: str, live_text: str,
) -> None:
    live = tmp_path / "live"
    live.mkdir(exist_ok=True)
    (live / "settings.json").write_text(live_text, encoding="utf-8")

    def stage(dst: Path, *, category: str = "all") -> None:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "settings.json").write_text(claude.filter_claude_settings(repo_text), encoding="utf-8")

    monkeypatch.setattr(claude, "stage_projection", stage)
    monkeypatch.setattr(status_cmd, "tool_home", lambda tool: live)
    status_cmd.status_tool("claude")


def test_a_path_this_acg_would_rewrite_is_a_difference(
    machine: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """DESKTOP-CAO4ENQ: applied with 1.0.130, the other machine's path stayed in live."""
    legacy = _settings("~/ai-config/data/claude/mods")

    _status_claude(tmp_path, monkeypatch, legacy, legacy)

    out = capsys.readouterr().out
    assert "No differences found" not in out
    assert "this acg writes it differently" in out
    assert "~/.acg-data/claude/mods" in out


def test_after_apply_the_same_file_is_no_difference(
    machine: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    repo = _settings("~/ai-config/data/claude/mods")
    applied = claude.merge_claude_settings(claude.filter_claude_settings(repo), repo)

    _status_claude(tmp_path, monkeypatch, repo, applied)

    assert "No differences found" in capsys.readouterr().out


PUSH = shared_hooks.SharedHook("after-push", "PostToolUse", 'bash "$HOME/x.sh"', matcher="Bash", timeout=5)


@pytest.fixture
def codex_home(machine: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".codex"
    home.mkdir()
    monkeypatch.setattr(shared_hooks, "codex_homes", lambda: [home])
    monkeypatch.setattr(shared_hooks, "load_all", lambda: [PUSH])
    return home


def test_codex_hooks_written_by_an_older_acg_are_pending(
    codex_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    shared_hooks.project_codex()
    # 換成 Windows 的新版:同一份定義,寫法變成走轉接器
    monkeypatch.setattr(shared_hooks, "_windows", lambda: True)

    assert status_cmd._report_shared_hook_projection("codex")
    out = capsys.readouterr().out
    assert "hooks.json" in out
    assert "agy-hooks" in out and "after-push.json" in out

    shared_hooks.project_tools(["codex"])
    assert not status_cmd._report_shared_hook_projection("codex")


def test_agy_hooks_that_apply_would_change_are_pending(
    codex_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config import remember_hosts

    monkeypatch.setattr(remember_hosts, "AGY_HOOKS", tmp_path / ".gemini" / "config" / "hooks.json")

    assert remember_hosts.AGY_HOOKS in shared_hooks.pending_projection("agy")
    shared_hooks.project_tools(["agy"])
    assert shared_hooks.pending_projection("agy") == []


def test_claude_has_no_separate_hook_projection(codex_home: Path) -> None:
    # Claude 的共用 hook 寫在 settings.json 裡,由上面的重新渲染比對涵蓋
    assert not status_cmd._report_shared_hook_projection("claude")


def test_a_hook_check_that_could_not_run_is_not_called_consistent(
    machine: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable(tool: str) -> list:
        raise OSError("permission denied")

    monkeypatch.setattr(shared_hooks, "pending_projection", unreadable)
    assert status_cmd._report_shared_hook_projection("codex")


def test_a_broken_hook_definition_is_reported_not_swallowed(
    machine: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    def broken(*args, **kwargs):
        raise shared_hooks.DefinitionError("bad.json: no command")

    current = _settings("~/.acg-data/claude/mods")
    monkeypatch.setattr(claude, "merge_claude_settings", broken)
    _status_claude(tmp_path, monkeypatch, current, current)

    out = capsys.readouterr()
    assert "No differences found" not in out.out
    assert "bad.json" in out.out + out.err
