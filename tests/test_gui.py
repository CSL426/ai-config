"""Unit tests for the GUI bridge layer (ai_config/gui.py).

GuiApi wraps the CLI in-process: it whitelists commands, captures
stdout/stderr, strips ANSI, and pre-feeds stdin for push confirmations.
These tests monkeypatch ai_config.__main__.main so no repo is needed.
"""

import threading
from pathlib import Path

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


def test_package_skills_rejects_bad_input(api: GuiApi) -> None:
    assert api.package_skills("not-a-list")["code"] == 1
    result = api.package_skills([])
    assert result["code"] == 1
    assert result["zips"] == []


def test_package_skills_packages_each_and_reports(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import ai_config.gui as gui_mod
    import ai_config.package as package_mod

    def fake_package(name, out_dir):
        if name == "missing":
            raise package_mod.SkillNotFoundError(name)
        return out_dir / f"{name}.zip"

    monkeypatch.setattr(package_mod, "package_skill", fake_package)
    monkeypatch.setattr(gui_mod, "_package_output_dir", lambda: tmp_path)

    result = api.package_skills(["wiki888", "missing"])
    assert result["code"] == 1
    assert result["zips"] == [str(tmp_path / "wiki888.zip")]
    assert "✓ 已打包" in result["output"]
    assert "✗ 找不到技能:missing" in result["output"]

    ok = api.package_skills(["wiki888"])
    assert ok["code"] == 0
    assert ok["zips"] == [str(tmp_path / "wiki888.zip")]


def test_package_output_dir_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ai_config.gui import _package_output_dir

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert _package_output_dir() == tmp_path
    (tmp_path / "Downloads").mkdir()
    assert _package_output_dir() == tmp_path / "Downloads"


def test_get_info_reports_version_and_tools(api: GuiApi) -> None:
    info = api.get_info()
    assert info["tools"] == ["claude", "codex", "agy"]
    assert info["version"]
    assert info["repo"]
