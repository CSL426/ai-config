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
    """Detect a private console, including a frozen onefile bootloader."""
    if sys.platform != "win32":
        return False
    if os.environ.get("AI_CONFIG_FORCE_DOUBLE_CLICK") == "1":
        return True
    return owns_console()


def owns_console() -> bool:
    """Only claim consoles attached to us and our own onefile bootloader."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        get_processes = ctypes.windll.kernel32.GetConsoleProcessList
        get_processes.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        get_processes.restype = wintypes.DWORD
        buffer = (wintypes.DWORD * 64)()
        count = get_processes(buffer, len(buffer))
        # Zero is failure; a count above capacity means no PIDs were returned.
        if not 0 < count <= len(buffer):
            return False
        attached = set(buffer[:count])
        if attached == {os.getpid()}:
            return True
        # A shell also gives two processes. Verify both the parent PID and
        # executable before treating it as PyInstaller's onefile bootloader.
        return (
            bool(getattr(sys, "frozen", False))
            and attached == {os.getpid(), os.getppid()}
            and _parent_uses_same_executable()
        )
    except (AttributeError, OSError, ValueError):
        return False


def _parent_uses_same_executable() -> bool:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = kernel.OpenProcess(0x1000, False, os.getppid())
    if not process:
        return False
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if not kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return False
        return os.path.samefile(path.value, sys.executable)
    finally:
        kernel.CloseHandle(process)


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
    if sys.argv[1:2] in (["__git-credential"], ["__memory-project-entry"]):
        # git 把我們當 credential helper 呼叫:stdout 是憑證協定的通道,
        # 不能印任何提示;從沒有主控台的 Desktop 叫起來時 Windows 會配一個新
        # 主控台,看起來像雙擊,若照一般流程就會往 stdout 印「按 Enter 關閉」
        return console_main()
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
    gui_launch = sys.argv[1:] in (
        ["gui"],
        ["desktop"],
        ["gui", "--wait"],
        ["desktop", "--wait"],
    )
    if keep_window and (not gui_launch or code != 0):
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
            "在 PowerShell 執行 acg gui --wait 可以看到完整錯誤訊息。",
            file=sys.stderr,
        )
        return 1


def _pause_before_closing() -> None:
    # 提示走 stderr:stdout 可能是別的程式在讀的管線
    print(file=sys.stderr)
    try:
        sys.stderr.write("按 Enter 關閉視窗…")
        sys.stderr.flush()
        input()
    except (EOFError, KeyboardInterrupt):
        pass
