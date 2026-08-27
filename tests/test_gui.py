"""Unit tests for the GUI bridge layer (ai_config/gui.py).

GuiApi wraps the CLI in-process: it whitelists commands, captures
stdout/stderr, strips ANSI, and pre-feeds stdin for push confirmations.
These tests monkeypatch ai_config.__main__.main so no repo is needed.
"""

import threading

import pytest

import ai_config.__main__ as cli
from ai_config.gui import GuiApi


@pytest.fixture
def api() -> GuiApi:
    return GuiApi()


def test_run_rejects_unknown_command(api: GuiApi) -> None:
    result = api.run("reset")
    assert result["code"] == 1
    assert "not allowed" in result["output"]


def test_run_rejects_unknown_tool(api: GuiApi) -> None:
    result = api.run("status", tool="vim")
    assert result["code"] == 1
    assert "Unknown tool" in result["output"]


def test_run_captures_stdout_and_strips_ansi(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_main(argv):
        print("\033[0;32m✓\033[0m done")
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    result = api.run("status", tool="claude")
    assert result["code"] == 0
    assert result["output"] == "✓ done\n"


def test_run_passes_command_and_tool(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    api.run("apply", tool="codex")
    assert seen["argv"] == ["apply", "codex"]


def test_run_reports_nonzero_exit(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "main", lambda argv: 3)
    assert api.run("pull")["code"] == 3


def test_run_converts_systemexit_and_exception(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exits(argv):
        raise SystemExit(2)

    monkeypatch.setattr(cli, "main", exits)
    assert api.run("status")["code"] == 2

    def explodes(argv):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "main", explodes)
    result = api.run("status")
    assert result["code"] == 1
    assert "boom" in result["output"]


def test_push_prefeeds_stdin_confirmation(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_main(argv):
        answer = input("Push these changes? [y/N] ")
        return 0 if answer.strip() == "y" else 1

    monkeypatch.setattr(cli, "main", fake_main)
    assert api.run("push")["code"] == 0


def test_run_is_serialized_by_lock(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_main(argv):
        entered.set()
        release.wait(timeout=5)
        return 0

    monkeypatch.setattr(cli, "main", slow_main)
    first = threading.Thread(target=api.run, args=("status",))
    first.start()
    assert entered.wait(timeout=5)
    busy = api.run("status")
    release.set()
    first.join(timeout=5)
    assert busy["code"] == 1
    assert "另一個動作" in busy["output"]


def test_get_info_reports_version_and_tools(api: GuiApi) -> None:
    info = api.get_info()
    assert info["tools"] == ["claude", "codex", "agy"]
    assert info["version"]
    assert info["repo"]
