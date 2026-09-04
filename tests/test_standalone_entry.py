"""Double-click launch handling for the standalone executable."""

import builtins

import pytest

from ai_config import cli


def test_double_click_pauses_before_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: False)
    monkeypatch.setattr(cli, "console_main", lambda: 3)
    monkeypatch.setattr(builtins, "input", lambda prompt="": prompts.append(prompt))
    monkeypatch.setattr(cli.sys, "argv", ["ai-config.exe"])

    assert cli.standalone_main() == 3
    assert prompts and "Enter" in prompts[0]


def test_shell_launch_does_not_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: False)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: False)
    monkeypatch.setattr(cli, "console_main", lambda: 0)

    def no_input(prompt: str = "") -> str:
        raise AssertionError("input() must not be called")

    monkeypatch.setattr(builtins, "input", no_input)

    assert cli.standalone_main() == 0


def test_double_click_survives_closed_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: False)
    monkeypatch.setattr(cli, "console_main", lambda: 0)

    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)

    assert cli.standalone_main() == 0


def test_not_double_click_outside_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert cli.launched_by_double_click() is False


def test_double_click_opens_gui_when_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_config.commands import gui as gui_module

    opened = []
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: True)
    monkeypatch.setattr(gui_module, "run_gui", lambda: opened.append(True) or 0)
    monkeypatch.setattr(cli.sys, "argv", ["ai-config.exe"])

    def unexpected() -> int:
        raise AssertionError("console_main must not run for a GUI launch")

    monkeypatch.setattr(cli, "console_main", unexpected)

    assert cli.standalone_main() == 0
    assert opened == [True]


def test_double_click_with_arguments_stays_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: True)
    monkeypatch.setattr(cli, "console_main", lambda: 0)
    monkeypatch.setattr(cli.sys, "argv", ["ai-config.exe", "status"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")

    assert cli.standalone_main() == 0


def test_gui_index_prefers_pyinstaller_bundle(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config.commands import gui as gui_module

    bundled = tmp_path / "gui_assets"
    bundled.mkdir()
    (bundled / "index.html").write_text("<h1>bundled</h1>", encoding="utf-8")
    monkeypatch.setattr(gui_module.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert gui_module.gui_index_path() == bundled / "index.html"


def test_gui_index_falls_back_to_package_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_config.commands import gui as gui_module

    monkeypatch.delattr(gui_module.sys, "_MEIPASS", raising=False)
    assert gui_module.gui_index_path() == gui_module._ASSETS_DIR / "index.html"


def test_double_click_holds_window_when_gui_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import gui as gui_module

    prompts: list[str] = []
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: True)
    monkeypatch.setattr(gui_module, "run_gui", lambda: 1)
    monkeypatch.setattr(cli.sys, "argv", ["ai-config.exe"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": prompts.append(prompt))

    # 開不起來時視窗必須留住,否則使用者只看到閃一下
    assert cli.standalone_main() == 1
    assert prompts


def test_double_click_reports_a_gui_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ai_config.commands import gui as gui_module

    monkeypatch.setattr(cli, "launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: True)
    monkeypatch.setattr(cli.sys, "argv", ["ai-config.exe"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")

    def explode() -> int:
        raise RuntimeError("WebView2 runtime missing")

    monkeypatch.setattr(gui_module, "run_gui", explode)

    assert cli.standalone_main() == 1
    err = capsys.readouterr().err
    assert "WebView2 runtime missing" in err


def test_gui_reports_a_webview_start_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys as real_sys
    import types

    from ai_config.commands import gui as gui_module

    index = tmp_path / "index.html"
    index.write_text("<h1>x</h1>", encoding="utf-8")
    monkeypatch.setattr(gui_module, "gui_index_path", lambda: index)
    monkeypatch.setattr(gui_module.sys, "platform", "win32")

    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: None

    def boom() -> None:
        raise RuntimeError("no runtime")

    fake.start = boom
    monkeypatch.setitem(real_sys.modules, "webview", fake)

    assert gui_module.run_gui() == 1
    output = capsys.readouterr()
    assert "WebView2" in output.out + output.err


def test_bundled_build_opens_the_app_without_double_click_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import gui as gui_module

    opened = []
    # 偵測失敗(回 False)時仍然要開視窗:那個判斷只有一次 Win32 呼叫,
    # 判斷錯就等於視窗一閃而過,不能拿它當開不開 GUI 的條件
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: False)
    monkeypatch.setattr(cli, "gui_assets_bundled", lambda: True)
    monkeypatch.setattr(gui_module, "run_gui", lambda: opened.append(True) or 0)
    monkeypatch.setattr(cli.sys, "argv", ["ai-config.exe"])

    assert cli.standalone_main() == 0
    assert opened == [True]


def test_double_click_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setenv("AI_CONFIG_FORCE_DOUBLE_CLICK", "1")
    assert cli.launched_by_double_click() is True


def test_desktop_is_an_alias_for_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    import ai_config.__main__ as main_module
    from ai_config.commands import gui as gui_module

    seen = []
    monkeypatch.setattr(gui_module, "run_gui", lambda: seen.append("ran") or 0)

    assert main_module.main(["desktop"]) == 0
    assert main_module.main(["gui"]) == 0
    assert seen == ["ran", "ran"]
