"""GUI entry point: a pywebview window bridging to the CLI internals.

The frontend (gui/ at the repo root, Vite + TypeScript) is built into
ai_config/gui_assets/ and loaded as a local file. Frontend calls arrive
through pywebview's js_api bridge as methods on GuiApi.
"""

import contextlib
import io
import re
import sys
import threading
from pathlib import Path

from .console import log_error
from .paths import ALL_TOOLS

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# 白名單:GUI 只開放無互動提示的命令;push 的確認由前端對話框負責。
_ALLOWED_COMMANDS = ("status", "apply", "pull", "push")
_ASSETS_DIR = Path(__file__).resolve().parent / "gui_assets"

WINDOW_TITLE = "acg — AI 設定同步"


class GuiApi:
    """Methods exposed to the frontend via pywebview's js_api."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get_info(self) -> dict:
        from .paths import SCRIPT_DIR
        from .version import current_version

        return {
            "version": current_version() or "unknown",
            "repo": str(SCRIPT_DIR),
            "tools": list(ALL_TOOLS),
        }

    def run(self, cmd: str, tool: str = "all") -> dict:
        if cmd not in _ALLOWED_COMMANDS:
            return {"code": 1, "output": f"✗ Command not allowed from GUI: {cmd}"}
        if tool != "all" and tool not in ALL_TOOLS:
            return {"code": 1, "output": f"✗ Unknown tool: {tool}"}
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured(cmd, tool)
        finally:
            self._lock.release()

    def _run_captured(self, cmd: str, tool: str) -> dict:
        from . import __main__ as cli

        buf = io.StringIO()
        stdin_backup = sys.stdin
        try:
            if cmd == "push":
                # push 會用 input() 要求確認;GUI 已先跳過確認框,這裡預填同意。
                sys.stdin = io.StringIO("y\n" * 8)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    code = cli.main([cmd, tool])
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                except Exception as exc:  # noqa: BLE001 — 任何內部錯誤都要回到前端
                    print(f"✗ Unexpected error: {exc}")
                    code = 1
        finally:
            sys.stdin = stdin_backup
        return {"code": code, "output": _ANSI_RE.sub("", buf.getvalue())}


def run_gui() -> int:
    index = _ASSETS_DIR / "index.html"
    if not index.is_file():
        log_error(
            "GUI assets not found. Build them first:\n"
            "  cd gui && pnpm install && pnpm build"
        )
        return 1
    try:
        import webview
    except ImportError:
        log_error('pywebview is not installed. Install with: pip install "ai-config[gui]"')
        return 1

    webview.create_window(
        WINDOW_TITLE,
        str(index),
        js_api=GuiApi(),
        width=880,
        height=680,
        min_size=(640, 480),
    )
    webview.start()
    return 0
