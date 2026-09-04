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
    try:
        import ctypes

        attached = (ctypes.c_uint * 2)()
        return ctypes.windll.kernel32.GetConsoleProcessList(attached, 2) == 1
    except (AttributeError, OSError, ValueError):
        return False


def gui_assets_bundled() -> bool:
    """True when this build ships the GUI frontend (Windows release only)."""
    from ai_config.commands.gui import gui_index_path

    return gui_index_path().is_file()


def standalone_main() -> int:
    """Entry point for the PyInstaller build.

    A double-clicked exe has no arguments and its console dies with the
    process, so the CLI help would flash past unread. Open the GUI instead
    when this build carries it, and otherwise explain the situation and hold
    the window open. Anything launched from a shell behaves like the CLI.
    """
    keep_window = launched_by_double_click()
    if keep_window and len(sys.argv) <= 1:
        if gui_assets_bundled():
            code = _run_gui_guarded()
            if code == 0:
                return code
            # 開不起來時不能直接結束:雙擊的 console 會立刻消失,
            # 使用者只會看到視窗閃一下,拿不到任何線索
        else:
            print("acg 是命令列工具,請在 PowerShell 或 cmd 視窗裡執行,例如:")
            print("    acg status")
            print()
            code = console_main()
        _pause_before_closing()
        return code
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
