import os
import sys

_ENTRYPOINT_NAMES = {"ai-config", "acg"}


def console_main() -> int:
    # argv[0] is only trustworthy when launched via an installed script;
    # pytest and `python -m ai_config` would otherwise leak "__main__.py".
    name = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    name = name.removesuffix(".exe")
    if name not in _ENTRYPOINT_NAMES:
        name = "ai-config"
    os.environ.setdefault("AI_CONFIG_ENTRYPOINT", name)
    from ai_config import __main__ as command

    command.ENTRYPOINT = os.environ["AI_CONFIG_ENTRYPOINT"]
    try:
        return command.main()
    except KeyboardInterrupt:
        # PyInstaller 對未攔截例外會跳錯誤視窗;Ctrl+C 應該安靜退出。
        print()
        print("Cancelled.", file=sys.stderr)
        return 130


def launched_by_double_click() -> bool:
    """Windows only: True when this process is the sole owner of its console.

    Explorer spawns a fresh console for a double-clicked exe and destroys it
    the moment the process exits, so whatever we printed vanishes and the
    user sees a window flash by. A console shared with a shell reports more
    than one attached process.
    """
    if sys.platform != "win32":
        return False
    if os.environ.get("AI_CONFIG_FORCE_DOUBLE_CLICK") == "1":
        return True
    try:
        import ctypes

        # 緩衝區必須夠大:太小時這個 API 會失敗而不是回報總數,
        # 用 2 個元素在有多個附著行程時只會拿到 0
        buffer = (ctypes.c_uint * 64)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(buffer, 64)
    except (AttributeError, OSError, ValueError):
        return False
    # 0 代表呼叫失敗(通常是沒有主控台),不是「沒有行程」
    return count == 1


def gui_assets_bundled() -> bool:
    """True when this build ships the GUI frontend (Windows release only)."""
    from ai_config.commands.gui import gui_index_path

    return gui_index_path().is_file()


def standalone_main() -> int:
    """Entry point for the PyInstaller build.

    Running with no arguments means no command was given, which is exactly
    what a double-click produces. When this build carries the desktop app,
    that opens it — deliberately NOT gated on detecting the double-click,
    because that detection is a single Win32 call that can be wrong, and
    being wrong there means the window vanishes with nothing to read.
    Typing `acg` bare in a shell opens the app too, which is a reasonable
    reading of the bare command.

    Everything else behaves as the CLI always has; the window is only held
    open when this process owns the console, since that console dies with
    the process and would take the output with it.
    """
    no_arguments = len(sys.argv) <= 1
    if no_arguments and gui_assets_bundled():
        code = _run_gui_guarded()
        if code == 0:
            return code
        # 開不起來時不能直接結束:雙擊的 console 會隨行程消失,
        # 使用者只會看到視窗閃一下,拿不到任何線索
        _pause_before_closing()
        return code

    keep_window = launched_by_double_click()
    if keep_window and no_arguments:
        print("acg 是命令列工具,請在 PowerShell 或 cmd 視窗裡執行,例如:")
        print("    acg status")
        print()
    code = console_main()
    if keep_window:
        _pause_before_closing()
    return code


def _run_gui_guarded() -> int:
    """Start the desktop app, turning any failure into a readable message."""
    from ai_config.commands.gui import run_gui

    try:
        return run_gui()
    except Exception as exc:  # noqa: BLE001 - 最後一道防線,不能讓視窗直接消失
        print(f"桌面版啟動失敗:{type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "在 PowerShell 執行 acg gui 可以看到完整錯誤訊息。",
            file=sys.stderr,
        )
        return 1


def _pause_before_closing() -> None:
    print()
    try:
        input("按 Enter 關閉視窗…")
    except (EOFError, KeyboardInterrupt):
        pass
