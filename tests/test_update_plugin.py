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


@pytest.fixture(autouse=True)
def _unreleased(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests are about install versus update; pinning has its own below."""
    monkeypatch.setattr(claude_plugin, "_release_ref", lambda: None)


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
def test_the_installed_version_brings_the_plugin_and_can_be_told_not_to(script: str) -> None:
    """Install and update both end with the new executable updating /acg.

    The installer comes from the release being installed, so the step runs
    in the new version even when an older acg started the update.
    """
    text = (REPO_ROOT / script).read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if "NO_PLUGIN" in line)

    assert "Installation" not in line
    assert "__claude-plugin" in text
    assert "__refresh-hooks" in text


def test_the_marketplace_follows_this_release_not_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """On main, Claude Code moved /acg ahead of the CLI by itself."""
    monkeypatch.setattr(claude_plugin, "_release_ref", lambda: "v1.0.106")
    seen = _fake(monkeypatch, [], [])

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [
        ["plugin", "marketplace", "add", "CSL426/ai-config#v1.0.106"],
        ["plugin", "install", "acg@acg", "--scope", "user"],
    ]


@pytest.mark.parametrize("market", [
    {"name": "acg"},                       # 以前加的,追 main
    {"name": "acg", "ref": "v1.0.105"},   # 上一版
])
def test_a_marketplace_on_another_ref_is_moved_to_this_release(
    monkeypatch: pytest.MonkeyPatch, market: dict,
) -> None:
    """Adding the same name again keeps the old ref, so it is removed and re-added."""
    monkeypatch.setattr(claude_plugin, "_release_ref", lambda: "v1.0.106")
    seen = _fake(monkeypatch, USER_ACG, [market])

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [
        ["plugin", "marketplace", "remove", "acg"],
        ["plugin", "marketplace", "add", "CSL426/ai-config#v1.0.106"],
        ["plugin", "install", "acg@acg", "--scope", "user"],
    ]


def test_a_marketplace_already_on_this_release_is_only_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_plugin, "_release_ref", lambda: "v1.0.106")
    seen = _fake(monkeypatch, USER_ACG, [{"name": "acg", "ref": "v1.0.106"}])

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [["plugin", "update", "acg@acg"]]


def test_a_version_without_a_tag_falls_back_to_the_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A source checkout reports a version that has not been released yet."""
    monkeypatch.setattr(claude_plugin, "_release_ref", lambda: "v9.9.9")
    seen: list = []

    def run(argv, **kwargs):
        args = list(argv[1:])
        seen.append(args)
        if args[-1:] == ["--json"]:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        code = 1 if args[-1].endswith("#v9.9.9") else 0
        return subprocess.CompletedProcess(argv, code, "done", "no such ref" if code else "")

    monkeypatch.setattr(claude_plugin.subprocess, "run", run)
    monkeypatch.setattr(claude_plugin, "claude_binary", lambda: "/bin/claude")
    monkeypatch.delenv(claude_plugin.OPT_OUT, raising=False)

    claude_plugin.ensure("CSL426/ai-config")

    assert _mutations(seen) == [
        ["plugin", "marketplace", "add", "CSL426/ai-config#v9.9.9"],
        ["plugin", "marketplace", "add", "CSL426/ai-config"],
        ["plugin", "install", "acg@acg", "--scope", "user"],
    ]


def test_the_release_ref_is_this_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_config import version

    monkeypatch.undo()
    monkeypatch.setattr(version, "current_version", lambda: "1.0.106")
    assert claude_plugin._release_ref() == "v1.0.106"
    monkeypatch.setattr(version, "current_version", lambda: "unknown")
    assert claude_plugin._release_ref() is None
