"""GUI bridge exposed to the frontend through pywebview's js_api.

The frontend (gui/ at the repo root, Vite + TypeScript) calls methods on
GuiApi. Desktop process lifecycle and shortcut management live in desktop.py.
"""

import contextlib
import io
import re
import secrets
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .console import log_error
from .gui_management import ManagementApi
from .paths import ALL_TOOLS
from .subproc import UTF8

PUSH_SCOPES = (*ALL_TOOLS, "all", "memory")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# 白名單:GUI 只開放無互動提示的命令;push 的確認由前端對話框負責。
_ALLOWED_COMMANDS = ("status", "apply", "pull", "push")


class _PromptInput(io.TextIOBase):
    """Answer CLI prompts while retaining the exact review shown beforehand."""

    def __init__(
        self,
        output: io.StringIO,
        answer: Callable[[str], str],
    ) -> None:
        self._output = output
        self._answer = answer
        self.reviews: list[str] = []

    def readline(self, size: int = -1) -> str:
        review = self._output.getvalue()
        self.reviews.append(review)
        response = f"{self._answer(review)}\n"
        return response if size < 0 else response[:size]


class GuiApi(ManagementApi):
    """Methods exposed to the frontend via pywebview's js_api."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._push_preview: tuple[str, str, str] | None = None
        self._init_management()

    def get_info(self) -> dict:
        from .config import configured_remote_provider
        from .paths import CONFIG_ERROR, SCRIPT_DIR
        from .version import current_commit, current_version

        configured = CONFIG_ERROR is None and (SCRIPT_DIR / "claude").is_dir()
        provider = configured_remote_provider() if CONFIG_ERROR is None else "git"
        return {
            "version": current_version() or "unknown",
            "build_commit": current_commit() or "",
            "repo": str(SCRIPT_DIR),
            "provider": provider,
            "tools": list(ALL_TOOLS),
            "configured": configured,
            "config_error": CONFIG_ERROR or "",
        }

    def github_access(self) -> dict:
        """Why pushing is refused, and whether acg can fix it from here.

        Called on demand rather than at window open: it shells out to gh
        and asks GitHub, which is too slow to sit in the startup path.
        """
        from .ghauth import check_push_access, describe, device_login_available
        from .paths import SCRIPT_DIR

        status = check_push_access(self._redacted_remote(), SCRIPT_DIR)
        return {
            "device_login": device_login_available(),
            "repository": status.repository,
            "installed": status.installed,
            "logged_in": status.logged_in,
            "account": status.account,
            "accounts": status.accounts,
            "can_push": status.can_push,
            "actionable": status.actionable,
            "bound": status.bound,
            "lines": describe(status),
        }

    def github_start_login(self) -> dict:
        """Begin the device flow and open GitHub in the browser."""
        import webbrowser

        from .ghauth import (
            TERMINAL_LOGIN_HINT,
            GhAuthError,
            device_login_available,
            start_device_login,
        )

        if not device_login_available():
            # 正式建置才會注入 client ID;沒有的話講替代做法,不丟環境變數名稱
            return {
                "code": 1,
                "output": f"✗ 這個版本沒有內建瀏覽器登入。{TERMINAL_LOGIN_HINT}",
            }
        try:
            flow = start_device_login()
        except GhAuthError as exc:
            return {"code": 1, "output": f"✗ {exc}"}
        with contextlib.suppress(Exception):
            webbrowser.open(flow["verification_uri"])
        return {
            "code": 0,
            "device_code": flow["device_code"],
            "user_code": flow["user_code"],
            "verification_uri": flow["verification_uri"],
            "interval": flow["interval"],
            "output": "",
        }

    def github_terminal_login(self) -> dict:
        """Open a terminal window already running gh's browser login.

        The fallback for builds without a client ID: gh needs a terminal
        for its login, so give it one instead of telling people to type.
        """
        import shlex
        import shutil

        from .ghauth import login_command

        if shutil.which("gh") is None:
            return {
                "code": 1,
                "output": "✗ 找不到 GitHub CLI (gh),請先安裝:https://cli.github.com",
            }
        command = login_command()
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["cmd", "/c", "start", "acg GitHub 登入", "cmd", "/k", *command],
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
            elif sys.platform == "darwin":
                script = " ".join(shlex.quote(part) for part in command)
                subprocess.Popen(
                    [
                        "osascript",
                        "-e",
                        f'tell application "Terminal" to do script "{script}"',
                    ]
                )
            else:
                script = " ".join(shlex.quote(part) for part in command)
                for terminal in (
                    "x-terminal-emulator",
                    "gnome-terminal",
                    "konsole",
                    "xfce4-terminal",
                    "xterm",
                ):
                    if shutil.which(terminal):
                        subprocess.Popen(
                            [
                                terminal,
                                "-e",
                                f"bash -c {shlex.quote(script + '; exec bash')}",
                            ]
                        )
                        break
                else:
                    return {"code": 1, "output": "✗ 找不到可開啟的終端機程式"}
        except OSError as exc:
            return {"code": 1, "output": f"✗ 無法開啟終端機:{exc}"}
        return {
            "code": 0,
            "output": "已開啟終端機並啟動 GitHub 登入;完成後回到這裡按「重新檢查」,再選「改用 <帳號>」。",
        }

    def github_poll_login(self, device_code: str = "", interval: int = 5) -> dict:
        """One poll step; the page decides how long to keep waiting."""
        from .ghauth import (
            GhAuthError,
            bind_account,
            check_push_access,
            poll_device_login,
            store_token,
        )
        from .paths import SCRIPT_DIR

        if not isinstance(device_code, str) or not device_code:
            return {"code": 1, "status": "error", "output": "✗ 沒有登入請求"}
        try:
            token = poll_device_login(device_code, int(interval))
        except GhAuthError as exc:
            return {"code": 1, "status": "error", "output": f"✗ {exc}"}
        if token is None:
            return {"code": 0, "status": "pending", "output": ""}

        stored, detail = store_token(token)
        if not stored:
            return {"code": 1, "status": "error", "output": f"✗ {detail}"}
        # detail 是登入的帳號名;只綁在資料庫上,不動 gh 其他用途
        bound, why = bind_account(SCRIPT_DIR, detail)
        if not bound:
            return {"code": 1, "status": "error", "output": f"✗ 綁定帳號失敗:{why}"}
        status = check_push_access(self._redacted_remote(), SCRIPT_DIR)
        if status.can_push:
            return {
                "code": 0,
                "status": "done",
                "output": f"✓ 資料庫已綁定 {status.account},現在可以上傳",
            }
        return {
            "code": 1,
            "status": "error",
            "output": f"✗ {status.detail}",
        }

    def github_use_account(self, account: str = "") -> dict:
        """Bind an account gh already knows to the data repository."""
        from .ghauth import bind_account, check_push_access
        from .paths import SCRIPT_DIR

        if not isinstance(account, str) or not account.strip():
            return {"code": 1, "output": "✗ 沒有指定帳號"}
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            ok, detail = bind_account(SCRIPT_DIR, account.strip())
            if not ok:
                return {"code": 1, "output": f"✗ 綁定帳號失敗:{detail}"}
            status = check_push_access(self._redacted_remote(), SCRIPT_DIR)
            if not status.can_push:
                return {
                    "code": 1,
                    "output": f"✗ {status.detail}",
                }
            return {"code": 0, "output": f"✓ 資料庫已綁定 {account},現在可以上傳"}
        finally:
            self._lock.release()

    def settings_info(self) -> dict:
        """Everything the settings screen shows, read from local config only.

        Deliberately does no network work: this runs when the window opens,
        and a dry-run push against the remote would stall it. Whether the
        remote accepts writes is answered by running a real command.
        """
        from .config import (
            ConfigError,
            configured_gdrive_folder,
            configured_gdrive_folder_id,
            configured_gdrive_space,
            configured_remote_provider,
        )
        from .paths import CONFIG_ERROR, SCRIPT_DIR

        provider = "git"
        space = "visible"
        folder = ""
        folder_url = ""
        if CONFIG_ERROR is None:
            try:
                provider = configured_remote_provider()
                space = configured_gdrive_space()
                folder = configured_gdrive_folder()
                folder_id = configured_gdrive_folder_id()
                if folder_id and space == "visible":
                    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
            except ConfigError:
                pass

        signed_in = False
        if provider == "gdrive":
            from .gdrive import load_token

            signed_in = bool((load_token() or {}).get("access_token"))

        return {
            "provider": provider,
            "repo": str(SCRIPT_DIR),
            "remote_url": self._redacted_remote(),
            "gdrive_space": space,
            "gdrive_folder": folder,
            "gdrive_folder_url": folder_url,
            "signed_in": signed_in,
        }

    @staticmethod
    def _redacted_remote() -> str:
        from .commands.sync import _GIT_URL_CREDENTIALS
        from .paths import SCRIPT_DIR

        result = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True, **UTF8,
            check=False,
        )
        if result.returncode != 0:
            return ""
        # 遠端 URL 可能內嵌帳密,顯示前一律遮掉
        return _GIT_URL_CREDENTIALS.sub(r"\1***@", result.stdout.strip())

    def open_data_dir(self) -> dict:
        """Reveal the local data repository in the desktop file manager."""
        from .paths import SCRIPT_DIR

        if not SCRIPT_DIR.is_dir():
            return {"code": 1, "output": f"✗ 找不到資料夾:{SCRIPT_DIR}"}
        if sys.platform == "win32":
            opener = ["explorer", str(SCRIPT_DIR)]
        elif sys.platform == "darwin":
            opener = ["open", str(SCRIPT_DIR)]
        else:
            opener = ["xdg-open", str(SCRIPT_DIR)]
        try:
            # explorer 開啟成功時仍可能回非 0,所以不看 returncode
            subprocess.Popen(
                opener,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return {"code": 1, "output": f"✗ 無法開啟資料夾:{exc}"}
        return {"code": 0, "output": f"✓ 已開啟 {SCRIPT_DIR}"}

    def config_info(self) -> dict:
        """Return the same read-only overview as ``acg config``."""
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured(["config"])
        finally:
            self._lock.release()

    def setup_repo(self, repo_url: str, data_dir: str = "", account: str = "") -> dict:
        from .config import default_data_repo

        if not isinstance(repo_url, str) or not repo_url.strip():
            return {"code": 1, "output": "✗ 請貼上資料儲存庫的 Git URL"}
        if not isinstance(data_dir, str) or not isinstance(account, str):
            return {"code": 1, "output": "✗ 無效的本機目錄"}
        target = data_dir.strip() or str(default_data_repo())
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            argv = ["setup", "--data-dir", target, "--repo-url", repo_url.strip()]
            if account.strip():
                argv += ["--account", account.strip()]
            return self._run_captured(argv)
        finally:
            self._lock.release()

    def setup_gdrive(
        self,
        data_dir: str = "",
        gdrive_folder: str = "",
        gdrive_space: str = "",
    ) -> dict:
        from .config import GDRIVE_SPACES, default_data_repo

        if (
            not isinstance(data_dir, str)
            or not isinstance(gdrive_folder, str)
            or not isinstance(gdrive_space, str)
        ):
            return {"code": 1, "output": "✗ 無效的本機目錄"}
        space = gdrive_space.strip() or "visible"
        if space not in GDRIVE_SPACES:
            return {"code": 1, "output": "✗ 無效的儲存位置"}
        target = data_dir.strip() or str(default_data_repo())
        folder = gdrive_folder.strip()
        argv = [
            "setup",
            "--provider",
            "gdrive",
            "--data-dir",
            target,
            "--gdrive-space",
            space,
        ]
        # 隱藏空間沒有資料夾路徑可言
        if space == "visible" and folder:
            argv += ["--gdrive-folder", folder]
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured(argv)
        finally:
            self._lock.release()

    def relogin_gdrive(self) -> dict:
        """Renew OAuth authorization without changing the sync destination."""
        from .config import (
            ConfigError,
            configured_gdrive_space,
            configured_remote_provider,
        )
        from .gdrive import run_oauth_flow
        from .paths import CONFIG_ERROR

        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            buf = io.StringIO()
            code = 0
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    if CONFIG_ERROR is not None:
                        raise ConfigError(CONFIG_ERROR)
                    if configured_remote_provider() != "gdrive":
                        raise ConfigError("目前尚未設定使用 Google Drive")
                    # OAuth validates before atomically replacing the token;
                    # refreshing or deleting it first would lose it on failure.
                    run_oauth_flow(space=configured_gdrive_space())
                except Exception as exc:  # noqa: BLE001 — 錯誤要回報前端
                    log_error(f"Google Drive 重新登入失敗:{exc}")
                    code = 1
            return {"code": code, "output": _ANSI_RE.sub("", buf.getvalue())}
        finally:
            self._lock.release()

    def list_skills(self) -> dict:
        from .commands.share import shareable_skill_names
        from .package import available_skills

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
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
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

    def unshare_skills(self, names: "list[str]") -> dict:
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return {"code": 1, "output": "✗ 無效的技能清單"}
        if not names:
            return {"code": 1, "output": "⚠ 還沒有勾選任何技能"}
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            outputs: list[str] = []
            code = 0
            for name in names:
                result = self._run_captured(["unshare", name])
                outputs.append(result["output"])
                code = max(code, result["code"])
            return {"code": code, "output": "".join(outputs)}
        finally:
            self._lock.release()

    def package_skills(self, names: "list[str]") -> dict:
        from .package import SkillNotFoundError, package_skill

        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
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
        if cmd == "push":
            return {
                "code": 1,
                "output": "✗ 請先預覽上傳內容,再確認上傳。",
            }
        if cmd == "apply":
            # 套用只走預覽／確認,不留免確認通道
            return {
                "code": 1,
                "output": "✗ 請先預覽套用內容,再確認套用。",
            }
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}
        try:
            return self._run_captured([cmd, tool])
        finally:
            self._lock.release()

    @staticmethod
    def _push_argv(scope: str) -> list[str]:
        return ["memory", "push"] if scope == "memory" else ["push", scope]

    @staticmethod
    def _push_range(scope: str) -> tuple[list[str], list[str]]:
        """Uncommitted paths in scope, and every commit a push would carry."""
        from .commands.sync import _run_repo_git

        try:
            status = _run_repo_git("status", "--porcelain=v1", "--untracked-files=all")
            log = _run_repo_git("log", "--oneline", "@{upstream}..HEAD")
        except (OSError, subprocess.SubprocessError):
            return [], []
        changed = (
            [line[3:] for line in status.stdout.splitlines() if line.strip()]
            if status.returncode == 0
            else []
        )
        if scope != "all":
            changed = [path for path in changed if path.startswith(f"{scope}/")]
        commits = log.stdout.splitlines() if log.returncode == 0 else []
        return changed, commits

    def preview_push(self, tool: str = "all") -> dict:
        """Prepare a non-destructive push review for the GUI confirmation step.

        ``tool`` is the push scope: a tool, ``all`` or ``memory``.
        """
        empty = {
            "needs_confirmation": False,
            "token": "",
            "error": None,
            "scope": tool,
            "changed_paths": [],
            "outgoing_commits": [],
        }
        if tool not in PUSH_SCOPES:
            return {
                **empty,
                "code": 1,
                "output": f"✗ Unknown tool: {tool}",
                "error": "INVALID_ARGUMENT",
            }
        if not self._lock.acquire(blocking=False):
            return {
                **empty,
                "code": 1,
                "output": "⚠ 另一個動作正在執行中,請稍候再試。",
                "error": "BUSY",
            }
        try:
            # 新預覽讓先前所有待確認的操作失效
            self._discard_previews()
            reviews: list[str] = []
            result = self._run_captured(
                self._push_argv(tool),
                answer_prompt=lambda _review: "n",
                prompt_reviews=reviews,
            )
            if result["code"] != 0 or not reviews:
                return {
                    **empty,
                    **result,
                    "error": "GIT_BLOCKED" if result["code"] != 0 else None,
                }

            token = secrets.token_urlsafe(24)
            review = reviews[0]
            self._push_preview = (token, tool, review)
            changed, commits = self._push_range(tool)
            return {
                **empty,
                "code": 0,
                "output": review,
                "needs_confirmation": True,
                "token": token,
                "changed_paths": changed,
                "outgoing_commits": commits,
            }
        finally:
            self._lock.release()

    def confirm_push(self, tool: str, token: str) -> dict:
        """Push only when the fresh CLI review matches the preview exactly."""
        if not isinstance(token, str) or not token or self._push_preview is None:
            return {"code": 1, "output": "✗ 上傳預覽已失效,請重新預覽。"}
        expected_token, expected_tool, expected_review = self._push_preview
        if tool != expected_tool or not secrets.compare_digest(token, expected_token):
            return {"code": 1, "output": "✗ 上傳預覽已失效,請重新預覽。"}
        if not self._lock.acquire(blocking=False):
            return {"code": 1, "output": "⚠ 另一個動作正在執行中,請稍候再試。"}

        self._push_preview = None
        review_matched = False
        reviews: list[str] = []

        def confirm_if_unchanged(review: str) -> str:
            nonlocal review_matched
            review_matched = secrets.compare_digest(review, expected_review)
            return "y" if review_matched else "n"

        try:
            result = self._run_captured(
                self._push_argv(tool),
                answer_prompt=confirm_if_unchanged,
                prompt_reviews=reviews,
            )
        finally:
            self._lock.release()

        if not reviews or not review_matched:
            return {
                "code": 1,
                "output": (
                    result["output"] + "✗ 內容在預覽後已有變動,尚未上傳。請重新預覽。\n"
                ),
            }
        return result

    def check_update(self) -> dict:
        from .commands.update import _is_up_to_date, _latest_release_version
        from .version import current_version

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

    def _run_captured(
        self,
        argv: "list[str]",
        answer_prompt: "Callable[[str], str] | None" = None,
        prompt_reviews: "list[str] | None" = None,
    ) -> dict:
        from . import __main__ as cli

        buf = io.StringIO()
        stdin_backup = sys.stdin
        prompt_input: _PromptInput | None = None
        try:
            if answer_prompt is not None:
                prompt_input = _PromptInput(buf, answer_prompt)
                sys.stdin = prompt_input
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
            if prompt_reviews is not None and prompt_input is not None:
                prompt_reviews.extend(prompt_input.reviews)
        return {"code": code, "output": _ANSI_RE.sub("", buf.getvalue())}


def _package_output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()

