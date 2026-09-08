"""Double-click launch handling for the standalone executable."""

import builtins
import ctypes
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
    # 兩個名稱都要走同一條路徑;這裡不分離,才能觀察到實際呼叫
    monkeypatch.setattr(gui_module, "detach_and_run_gui", lambda: False)

    assert main_module.main(["desktop"]) == 0
    assert main_module.main(["gui"]) == 0
    assert seen == ["ran", "ran"]


def test_desktop_detaches_by_default_and_waits_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_config.__main__ as main_module
    from ai_config.commands import gui as gui_module

    ran = []
    monkeypatch.setattr(gui_module, "run_gui", lambda: ran.append("fg") or 0)
    monkeypatch.setattr(gui_module, "detach_and_run_gui", lambda: True)

    # 預設分離,終端機立刻回到提示字元
    assert main_module.main(["desktop"]) == 0
    assert ran == []

    # --wait 留在前景
    assert main_module.main(["desktop", "--wait"]) == 0
    assert ran == ["fg"]


def test_detach_skips_when_already_detached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import gui as gui_module

    monkeypatch.setenv(gui_module._DETACH_ENV, "1")
    # 子行程再呼叫一次不能又分離出去,否則會無限產生行程
    assert gui_module.detach_and_run_gui() is False


def test_detach_stays_foreground_without_assets(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config.commands import gui as gui_module

    monkeypatch.delenv(gui_module._DETACH_ENV, raising=False)
    monkeypatch.setattr(gui_module, "gui_index_path", lambda: tmp_path / "none")
    # 開不起來時要留在前景,才看得到錯誤原因
    assert gui_module.detach_and_run_gui() is False


def test_detach_starts_a_new_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config.commands import gui as gui_module

    index = tmp_path / "index.html"
    index.write_text("<h1>x</h1>", encoding="utf-8")
    monkeypatch.delenv(gui_module._DETACH_ENV, raising=False)
    monkeypatch.setattr(gui_module, "gui_index_path", lambda: index)

    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(gui_module.subprocess, "Popen", fake_popen)

    assert gui_module.detach_and_run_gui() is True
    assert seen["kwargs"]["env"][gui_module._DETACH_ENV] == "1"
    assert seen["kwargs"]["stdin"] == gui_module.subprocess.DEVNULL


def test_hide_console_is_a_noop_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import gui as gui_module

    monkeypatch.setattr(gui_module.sys, "platform", "linux")
    assert gui_module.hide_console() is False


def test_update_warns_when_running_an_unmanaged_copy(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ai_config.commands import update as update_module

    elsewhere = tmp_path / "Desktop" / "acg.exe"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("binary", encoding="utf-8")
    managed = tmp_path / "bin" / "ai-config"
    managed.parent.mkdir(parents=True)
    managed.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(update_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_module.sys, "executable", str(elsewhere))
    monkeypatch.setattr(update_module, "_standalone_candidate", lambda: managed)

    update_module._warn_if_updating_a_different_copy()
    text = capsys.readouterr().out + capsys.readouterr().err
    assert "不在安裝位置" in text or "Desktop" in text


def test_detach_hides_the_console_before_exiting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config.commands import gui as gui_module

    index = tmp_path / "index.html"
    index.write_text("<h1>x</h1>", encoding="utf-8")
    monkeypatch.delenv(gui_module._DETACH_ENV, raising=False)
    monkeypatch.setattr(gui_module, "gui_index_path", lambda: index)
    monkeypatch.setattr(gui_module.subprocess, "Popen", lambda *a, **k: object())

    hidden = []
    monkeypatch.setattr(gui_module, "hide_console", lambda: hidden.append(True) or True)

    # 分離出去的子行程沒有主控台可藏,所以父行程必須在結束前自己藏
    assert gui_module.detach_and_run_gui() is True
    assert hidden == [True]


@pytest.mark.parametrize(
    ("pids", "frozen", "parent_image", "expected"),
    [
        ([100], False, "app", True),
        ([100, 200], True, "app", True),
        ([200, 100], True, "app", True),
        ([100, 200], False, "app", False),
        ([100, 200], True, "shell", False),
        ([100, 300], True, "app", False),
        ([100, 200, 300], True, "app", False),
        ([200], True, "app", False),
        ([], True, "app", False),
        (list(range(65)), True, "app", False),
    ],
)
def test_console_ownership_preserves_shells(
    tmp_path, monkeypatch, pids, frozen, parent_image, expected
) -> None:
    from ai_config.commands import gui as gui_module

    executable = tmp_path / "app.exe"
    executable.touch()
    shell = tmp_path / "cmd.exe"
    shell.touch()

    def get_processes(buffer, capacity):
        if len(pids) <= capacity:
            for index, pid in enumerate(pids):
                buffer[index] = pid
        return len(pids)

    def query_image(handle, flags, buffer, size):
        buffer.value = str(executable if parent_image == "app" else shell)
        return True

    kernel = SimpleNamespace(
        GetConsoleProcessList=Mock(side_effect=get_processes),
        OpenProcess=Mock(return_value=0x100000001),
        QueryFullProcessImageNameW=Mock(side_effect=query_image),
        CloseHandle=Mock(),
        GetConsoleWindow=Mock(return_value=0x200000001),
    )
    user = SimpleNamespace(ShowWindow=Mock())
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(kernel32=kernel, user32=user), raising=False
    )
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(cli.sys, "executable", str(executable))
    monkeypatch.setattr(cli.os, "getpid", lambda: 100)
    monkeypatch.setattr(cli.os, "getppid", lambda: 200)
    monkeypatch.delenv("AI_CONFIG_FORCE_DOUBLE_CLICK", raising=False)

    assert cli.launched_by_double_click() is expected
    assert gui_module.hide_console() is expected
    if expected:
        user.ShowWindow.assert_called_once_with(0x200000001, 0)
    else:
        user.ShowWindow.assert_not_called()
    assert kernel.CloseHandle.call_count == kernel.QueryFullProcessImageNameW.call_count


@pytest.mark.parametrize("failure", ["open", "query", "missing"])
def test_console_ownership_rejects_unverifiable_parent(monkeypatch, failure) -> None:
    def get_processes(buffer, capacity):
        buffer[0], buffer[1] = 100, 200
        return 2

    kernel = SimpleNamespace(
        GetConsoleProcessList=Mock(side_effect=get_processes),
        OpenProcess=Mock(return_value=0 if failure == "open" else 1),
        QueryFullProcessImageNameW=Mock(return_value=failure != "query"),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False
    )
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.os, "getpid", lambda: 100)
    monkeypatch.setattr(cli.os, "getppid", lambda: 200)

    assert cli.owns_console() is False
    assert kernel.CloseHandle.call_count == (0 if failure == "open" else 1)


def test_frozen_detach_owns_its_bundle(tmp_path, monkeypatch) -> None:
    from ai_config.commands import gui as gui_module

    index = tmp_path / "index.html"
    index.touch()
    monkeypatch.delenv(gui_module._DETACH_ENV, raising=False)
    monkeypatch.setattr(gui_module, "gui_index_path", lambda: index)
    monkeypatch.setattr(gui_module, "hide_console", lambda: False)
    monkeypatch.setattr(gui_module.sys, "platform", "win32")
    monkeypatch.setattr(gui_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui_module.sys, "argv", ["acg.exe", "gui"])
    popen = Mock()
    monkeypatch.setattr(gui_module.subprocess, "Popen", popen)

    assert gui_module.detach_and_run_gui() is True
    assert popen.call_args.args[0] == [gui_module.sys.executable, "gui"]
    kwargs = popen.call_args.kwargs
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert kwargs["creationflags"] == 0x00000008
    assert kwargs["stdout"] == gui_module.subprocess.DEVNULL


@pytest.mark.parametrize("command", ["gui", "desktop"])
def test_successful_gui_shortcut_does_not_pause(monkeypatch, command) -> None:
    monkeypatch.setattr(cli, "launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "console_main", lambda: 0)
    monkeypatch.setattr(cli.sys, "argv", ["acg.exe", command])
    pause = Mock()
    monkeypatch.setattr(cli, "_pause_before_closing", pause)

    assert cli.standalone_main() == 0
    pause.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="requires a native console")
def test_native_console_recognizes_same_executable_parent(tmp_path) -> None:
    # Give the probe its own console so neither the test runner nor its shell
    # can be hidden. A matching Python parent models the onefile process pair.
    script = tmp_path / "console_probe.py"
    script.write_text(
        "import json, subprocess, sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from ai_config.cli import owns_console\n"
        "if len(sys.argv) == 1:\n"
        "    result = subprocess.run([sys.executable, __file__, 'child'],\n"
        "        capture_output=True, text=True, timeout=15, check=True)\n"
        "    print(result.stdout)\n"
        "else:\n"
        "    shared = owns_console()\n"
        "    sys.frozen = True\n"
        "    print(json.dumps({'shared': shared, 'frozen': owns_console()}))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == {"shared": False, "frozen": True}


def test_gui_restores_console_when_window_creation_fails(tmp_path, monkeypatch) -> None:
    from ai_config.commands import gui as gui_module

    index = tmp_path / "index.html"
    index.touch()
    monkeypatch.setattr(gui_module, "gui_index_path", lambda: index)
    monkeypatch.setattr(gui_module, "hide_console", lambda: True)
    show = Mock()
    monkeypatch.setattr(gui_module, "show_console", show)
    webview = SimpleNamespace(
        create_window=Mock(side_effect=RuntimeError("window failed")),
        start=Mock(),
    )
    monkeypatch.setitem(sys.modules, "webview", webview)

    assert gui_module.run_gui() == 1
    show.assert_called_once_with()
    webview.start.assert_not_called()


@pytest.mark.parametrize("error", [ImportError("missing"), RuntimeError("native")])
def test_gui_restores_console_when_runtime_import_fails(
    tmp_path, monkeypatch, error
) -> None:
    from ai_config.commands import gui as gui_module

    index = tmp_path / "index.html"
    index.touch()
    monkeypatch.setattr(gui_module, "gui_index_path", lambda: index)
    monkeypatch.setattr(gui_module, "hide_console", lambda: True)
    show = Mock()
    monkeypatch.setattr(gui_module, "show_console", show)
    original_import = builtins.__import__

    def import_module(name, *args, **kwargs):
        if name == "webview":
            raise error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)

    assert gui_module.run_gui() == 1
    show.assert_called_once_with()
