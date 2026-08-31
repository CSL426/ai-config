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

from ..console import log_error
from ..paths import ALL_TOOLS

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
        from ..config import configured_remote_provider
        from ..paths import CONFIG_ERROR, SCRIPT_DIR
        from ..version import current_version

        configured = CONFIG_ERROR is None and (SCRIPT_DIR / "claude").is_dir()
        provider = configured_remote_provider() if CONFIG_ERROR is None else "git"
        return {
            "version": current_version() or "unknown",
            "repo": str(SCRIPT_DIR),
            "provider": provider,
            "tools": list(ALL_TOOLS),
            "configured": configured,
            "config_error": CONFIG_ERROR or "",
        }

    def config_info(self) -> dict:
        """Return the same read-only overview as ``acg config``."""
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured(["config"])
        finally:
            self._lock.release()

    def setup_repo(self, repo_url: str, data_dir: str = "") -> dict:
        from ..config import default_data_repo

        if not isinstance(repo_url, str) or not repo_url.strip():
            return {"code": 1, "output": "✗ 請貼上資料儲存庫的 Git URL"}
        if not isinstance(data_dir, str):
            return {"code": 1, "output": "✗ 無效的本機目錄"}
        target = data_dir.strip() or str(default_data_repo())
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured(
                [
                    "setup",
                    "--data-dir",
                    target,
                    "--repo-url",
                    repo_url.strip(),
                ]
            )
        finally:
            self._lock.release()

    def setup_gdrive(self, data_dir: str = "") -> dict:
        from ..config import default_data_repo

        if not isinstance(data_dir, str):
            return {"code": 1, "output": "✗ 無效的本機目錄"}
        target = data_dir.strip() or str(default_data_repo())
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured(
                [
                    "setup",
                    "--provider",
                    "gdrive",
                    "--data-dir",
                    target,
                ]
            )
        finally:
            self._lock.release()

    def list_skills(self) -> dict:
        from ..package import available_skills
        from .share import shareable_skill_names

        shared = set(available_skills())
        shareable = set(shareable_skill_names())
        return {
            "skills": [
                {
                    "name": name,
                    "shared": name in shared,
                    "shareable": name in shareable,
                }
                for name in sorted(shared | shareable)
            ]
        }

    def share_skills(self, names: "list[str]") -> dict:
        if not isinstance(names, list) or not all(
            isinstance(n, str) for n in names
        ):
            return {"code": 1, "output": "✗ 無效的技能清單"}
        if not names:
            return {"code": 1, "output": "⚠ 還沒有勾選任何技能"}
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            outputs: list[str] = []
            code = 0
            for name in names:
                result = self._run_captured(["share", name])
                outputs.append(result["output"])
                code = max(code, result["code"])
            return {"code": code, "output": "".join(outputs)}
        finally:
            self._lock.release()

    def package_skills(self, names: "list[str]") -> dict:
        from ..package import SkillNotFoundError, package_skill

        if not isinstance(names, list) or not all(
            isinstance(n, str) for n in names
        ):
            return {"code": 1, "output": "✗ 無效的技能清單", "zips": []}
        if not names:
            return {"code": 1, "output": "⚠ 還沒有勾選任何技能", "zips": []}
        if not self._lock.acquire(blocking=False):
            return {
                "code": 1,
                "output": "⚠ 另一個動作正在執行中,請稍候再試。",
                "zips": [],
            }
        try:
            out_dir = _package_output_dir()
            zips: list[str] = []
            lines: list[str] = []
            code = 0
            for name in names:
                try:
                    zip_path = package_skill(name, out_dir)
                except SkillNotFoundError:
                    lines.append(f"✗ 找不到技能:{name}")
                    code = 1
                except OSError as exc:
                    lines.append(f"✗ 打包 {name} 失敗:{exc}")
                    code = 1
                else:
                    zips.append(str(zip_path))
                    lines.append(f"✓ 已打包:{zip_path}")
            return {"code": code, "output": "\n".join(lines) + "\n", "zips": zips}
        finally:
            self._lock.release()

    def run(self, cmd: str, tool: str = "all") -> dict:
        if cmd not in _ALLOWED_COMMANDS:
            return {"code": 1, "output": f"✗ Command not allowed from GUI: {cmd}"}
        if tool != "all" and tool not in ALL_TOOLS:
            return {"code": 1, "output": f"✗ Unknown tool: {tool}"}
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            # push 會用 input() 要求確認;GUI 已先跳過確認框,這裡預填同意。
            return self._run_captured([cmd, tool], feed_stdin=(cmd == "push"))
        finally:
            self._lock.release()

    def check_update(self) -> dict:
        from ..version import current_version
        from .update import _is_up_to_date, _latest_release_version

        current = current_version() or "unknown"
        try:
            latest = _latest_release_version()
        except Exception as exc:  # noqa: BLE001 — 網路錯誤要回報前端
            return {
                "code": 1,
                "current": current,
                "latest": "",
                "up_to_date": True,
                "output": f"✗ 無法檢查更新:{exc}",
            }
        return {
            "code": 0,
            "current": current,
            "latest": latest,
            "up_to_date": _is_up_to_date(current, latest),
            "output": "",
        }

    def run_update(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured(["update"])
        finally:
            self._lock.release()

    def _run_captured(self, argv: "list[str]", feed_stdin: bool = False) -> dict:
        from .. import __main__ as cli

        buf = io.StringIO()
        stdin_backup = sys.stdin
        try:
            if feed_stdin:
                sys.stdin = io.StringIO("y\n" * 8)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    code = cli.main(argv)
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                except Exception as exc:  # noqa: BLE001 — 任何內部錯誤都要回到前端
                    print(f"✗ Unexpected error: {exc}")
                    code = 1
        finally:
            sys.stdin = stdin_backup
        return {"code": code, "output": _ANSI_RE.sub("", buf.getvalue())}


def _package_output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


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
