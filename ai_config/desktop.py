"""Desktop process lifecycle, resource lookup, and shortcut management."""

import contextlib
import os
import subprocess
import sys
from pathlib import Path

from .console import log_error, log_info, log_success
from .gui_api import GuiApi
from .subproc import NATIVE

_ASSETS_DIR = Path(__file__).resolve().parent / "gui_assets"


def gui_index_path() -> Path:
    """Locate index.html in a source checkout or inside a PyInstaller bundle.

    --add-data unpacks gui_assets next to the frozen modules in sys._MEIPASS,
    which is not where __file__ points once the package is bundled.
    """
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if bundle_dir:
        bundled = Path(bundle_dir) / "gui_assets" / "index.html"
        if bundled.is_file():
            return bundled
    return _ASSETS_DIR / "index.html"


WINDOW_TITLE = "acg — AI 設定同步"


def _shortcut_target() -> Path:
    """The launcher `acg update` swaps in place, else the running executable.

    The running exe sits in versions/<x>/; a shortcut there points at an
    old version after the next update, and at nothing once it is pruned.
    """
    from . import paths

    launcher = paths.standalone_install_path()
    if launcher.is_file():
        return launcher
    return Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()


def _windows_shortcuts(target: Path) -> int:
    start_menu = (
        Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        / "Microsoft/Windows/Start Menu/Programs"
    )
    try:
        start_menu.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log_error(f"無法建立開始選單資料夾:{exc}")
        return 1
    # 桌面位置要問系統:OneDrive 會把它搬走,寫死 ~/Desktop 會建在沒人看的地方
    body = (
        f"$s.TargetPath = '{target}';"
        "$s.Arguments = 'gui';"
        f"$s.WorkingDirectory = '{target.parent}';"
        f"$s.IconLocation = '{target}';"
        "$s.Description = 'acg — AI 設定同步';"
        "$s.Save();"
    )
    script = (
        "$shell = New-Object -ComObject WScript.Shell;"
        "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), "
        f"'{start_menu}')) {{"
        "$s = $shell.CreateShortcut((Join-Path $dir 'acg.lnk'));"
        f"{body}"
        "}"
    )
    # 用 PowerShell 的 WScript.Shell 建 .lnk,不必額外依賴 pywin32
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True, **NATIVE,
        check=False,
    )
    if result.returncode != 0:
        log_error(f"建立捷徑失敗:{result.stderr.strip() or result.stdout.strip()}")
        return 1
    log_success("已在桌面與開始選單建立 acg 捷徑")
    return 0


def _linux_shortcuts(target: Path) -> int:
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=acg\n"
        "Comment=AI 設定同步\n"
        f"Exec={target} gui\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )
    home = Path.home()
    places = [home / ".local/share/applications"]
    # 有桌面資料夾才放;伺服器常常沒有,硬建一個沒人會看
    if (home / "Desktop").is_dir():
        places.append(home / "Desktop")
    try:
        for place in places:
            place.mkdir(parents=True, exist_ok=True)
            shortcut = place / "acg.desktop"
            shortcut.write_text(entry, encoding="utf-8")
            shortcut.chmod(0o755)
    except OSError as exc:
        log_error(f"建立捷徑失敗:{exc}")
        return 1
    log_success("已在應用程式選單" + ("與桌面" if len(places) > 1 else "") + "建立 acg 捷徑")
    return 0


def create_desktop_shortcut() -> int:
    """Put acg where people look for apps: the desktop and the app menu.

    The executable installs under ~/.local/bin, which nobody browses to;
    without this the desktop app is only reachable by typing a command,
    which is exactly the audience it is not for.
    """
    target = _shortcut_target()
    if not target.is_file():
        log_error(f"找不到執行檔:{target}")
        return 1
    if sys.platform == "win32":
        return _windows_shortcuts(target)
    if sys.platform.startswith("linux"):
        return _linux_shortcuts(target)
    log_error("捷徑目前支援 Windows 與 Linux")
    return 1


_DETACH_ENV = "AI_CONFIG_GUI_DETACHED"


def _missing_display() -> bool:
    return (
        sys.platform.startswith("linux")
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
        and os.environ.get("QT_QPA_PLATFORM") not in {"offscreen", "minimal"}
    )


def detach_and_run_gui() -> bool:
    """Relaunch this command detached, so a terminal is not held hostage.

    webview.start() blocks until the window closes, which leaves the shell
    that launched it unusable. Re-run the same command in a new session and
    return: the caller exits, the window lives on. Returns False when this
    process is already the detached child, or when relaunching is not
    possible, so the caller runs it in the foreground instead.
    """
    if os.environ.get(_DETACH_ENV) == "1":
        return False
    if not gui_index_path().is_file() or _missing_display():
        # 開不起來的話留在前景,才看得到原因
        return False

    environment = dict(os.environ, **{_DETACH_ENV: "1"})
    if getattr(sys, "frozen", False):
        # 打包版:sys.executable 就是這支 exe
        command = [sys.executable, *sys.argv[1:]]
        # The GUI outlives this process and must own its extracted bundle.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        command = [sys.executable, "-m", "ai_config", *sys.argv[1:]]

    kwargs: dict = {
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS,子行程不繼承這個主控台
        kwargs["creationflags"] = 0x00000008
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(command, **kwargs)
    except OSError:
        return False
    # Hide only our launch console while the onefile parent finishes cleanup.
    hide_console()
    return True


def show_console() -> None:
    """Bring back a console hidden by hide_console, so errors can be read."""
    if sys.platform != "win32":
        return
    with contextlib.suppress(AttributeError, OSError):
        import ctypes
        from ctypes import wintypes

        ctypes.windll.kernel32.GetConsoleWindow.restype = wintypes.HWND
        ctypes.windll.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, 5)  # SW_SHOW


def hide_console() -> bool:
    """Hide the console window this process owns, if it owns one.

    The exe is a console application because the CLI needs one, so Windows
    opens a black window before Python starts. Once the desktop window is
    up that console is just clutter, and closing it would kill the app.

    A onefile build attaches both its bootloader and Python child. The shared
    ownership check recognizes that pair while preserving an existing shell.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        from .cli import owns_console

        if not owns_console():
            return False
        ctypes.windll.kernel32.GetConsoleWindow.restype = wintypes.HWND
        ctypes.windll.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if not console:
            return False
        ctypes.windll.user32.ShowWindow(console, 0)  # SW_HIDE
    except (AttributeError, OSError, ValueError):
        return False
    return True


def run_gui() -> int:
    index = gui_index_path()
    if not index.is_file():
        if getattr(sys, "_MEIPASS", ""):
            # 打包版沒有原始碼可以 build,叫使用者 pnpm build 是無效的指示
            log_error("這個平台的執行檔沒有內建 Desktop 介面(目前只有 Windows 版有)。")
            log_info('改用 pip 安裝即可使用:pip install "ai-config[gui]"')
        elif (Path(__file__).resolve().parents[1] / "gui/package.json").is_file():
            log_error(
                "找不到 Desktop 介面的檔案,請先建置:\n"
                "  cd gui && pnpm install && pnpm build"
            )
        else:
            log_error("目前安裝的套件未包含 Desktop 介面資源。")
            log_info(f"缺少:{index}")
            log_info("請安裝含 GUI 資源的套件；在其他 checkout 建置不會更新這份套件。")
        return 1
    # 在 import webview 之前就藏:打包版載入 pywebview 要好幾秒,
    # 藏在後面的話那個黑視窗會杵在畫面上直到視窗開啟。
    # 失敗時 show_console() 會把它叫回來,訊息才看得到。
    hidden_console = hide_console()

    try:
        import webview
    except ImportError:
        if hidden_console:
            show_console()
        log_error('pywebview 尚未安裝,請執行:pip install "ai-config[gui]"')
        return 1
    except Exception as exc:  # noqa: BLE001 - native runtime loading can fail too
        if hidden_console:
            show_console()
        log_error(f"無法載入桌面介面:{type(exc).__name__}: {exc}")
        return 1

    if _missing_display():
        log_error("沒有可用的桌面連線(DISPLAY／WAYLAND_DISPLAY 未設定)。")
        log_info("請在圖形桌面的終端機執行 acg gui，或使用已設定圖形轉送的 SSH。")
        return 1

    # Windows: 分離工作列群組,避免顯示預設 Python 圖示
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "CSL426.ai-config.gui"
            )

    try:
        api = GuiApi()
        window = webview.create_window(
            WINDOW_TITLE,
            str(index),
            js_api=api,
            width=880,
            height=680,
            min_size=(640, 480),
        )
        # 視窗關閉時撤銷待確認的預覽並清掉暫存投影
        with contextlib.suppress(AttributeError):
            window.events.closed += api._discard_previews
        webview.start()
    except Exception as exc:  # noqa: BLE001 - pywebview 各平台丟的例外型別不一
        if hidden_console:
            show_console()
        log_error(f"無法開啟視窗:{type(exc).__name__}: {exc}")
        if sys.platform == "win32":
            # Windows 10 較舊的版本沒有預裝 WebView2,pywebview 就開不起來
            log_info("Windows 需要 Microsoft Edge WebView2 執行期,可從以下網址安裝:")
            log_info("https://developer.microsoft.com/microsoft-edge/webview2/")
        else:
            log_info(
                "Linux 需要系統的 WebKit2GTK 套件,"
                "例如 Debian/Ubuntu 的 gir1.2-webkit2-4.1 與 python3-gi"
            )
        return 1
    return 0
