"""Structured GUI memory/apply operations and single-use review lifecycle."""

import contextlib
import io
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from . import memory, paths, review
from .applyplan import StalePreview
from .locking import apply_lock


def outcome(code=0, output="", error=None, backup=None, recovery=False):
    return {"code": code, "output": output, "error": error,
            "backup_path": str(backup) if backup else None,
            "recovery_required": recovery}


def failure(exc):
    recovery = getattr(exc, "recovery_required", False)
    if recovery:
        error = "ROLLBACK_FAILED"
    elif isinstance(exc, StalePreview):
        error = "STALE_PREVIEW"
    elif isinstance(exc, ValueError):
        error = "INVALID_ARGUMENT"
    elif isinstance(exc, TimeoutError):
        error = "BUSY"
    elif isinstance(exc, OSError):
        error = "IO_ERROR"
    elif "Refusing" in str(exc) or "reparse" in str(exc):
        error = "UNSAFE_PATH"
    else:
        error = "CONFLICT"
    return outcome(1, str(exc), error, getattr(exc, "backup_path", None), recovery)


class ManagementApi:
    def _init_management(self):
        self._pending = None
        self._project = None
        self._locations = {}
        self._push_identity = None

    def _discard_previews(self):
        pending = self._pending
        self._pending = None
        self._push_preview = None
        self._push_identity = None
        if pending and pending["kind"] == "apply":
            pending["plan"].close()

    def _ensure_configured(self):
        if paths.CONFIG_ERROR or not (paths.SCRIPT_DIR / "claude").is_dir():
            raise ValueError(paths.CONFIG_ERROR or "請先設定資料庫")

    def _project_path(self, token):
        if not isinstance(token, str) or not self._project:
            raise StalePreview("請先選擇本機專案")
        expected, root, key, repository = self._project
        if (not secrets.compare_digest(token, expected)
                or str(paths.SCRIPT_DIR) != repository or not root.is_dir()
                or memory.project_root(root) != root
                or memory.project_key(root) != key):
            raise StalePreview("專案位置或遠端已變動，請重新選擇")
        return root

    def cancel_preview(self, token):
        if not isinstance(token, str):
            return outcome(1, "無效的預覽", "INVALID_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            current = self._pending["token"] if self._pending else ""
            push = self._push_preview[0] if self._push_preview else ""
            if token and (secrets.compare_digest(token, current)
                          or secrets.compare_digest(token, push)):
                self._discard_previews()
            return outcome(output="已取消預覽，尚未提交或上傳。")
        finally:
            self._lock.release()

    def select_memory_project(self):
        result = {"cancelled": False, "project_token": None, "root": None,
                  "key": None, "stable": False}
        if not self._lock.acquire(blocking=False):
            return {**result, **outcome(1, "另一個動作正在執行", "BUSY")}
        try:
            import webview

            self._ensure_configured()
            if not webview.windows:
                raise RuntimeError("沒有可用的原生視窗")
            selected = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
            if not selected:
                return {**result, **outcome(), "cancelled": True}
            chosen = Path(selected[0]).resolve(strict=True)
            if not chosen.is_dir():
                raise ValueError("請選擇專案資料夾")
            root = memory.project_root(chosen)
            key = memory.project_key(root)
            token = secrets.token_urlsafe(24)
            self._discard_previews()
            self._project = (token, root, key, str(paths.SCRIPT_DIR))
            return {**result, **outcome(), "project_token": token,
                    "root": str(root), "key": key.key, "stable": key.stable}
        except Exception as exc:  # noqa: BLE001 - native chooser errors vary by OS
            return {**result, **failure(exc)}
        finally:
            self._lock.release()

    def memory_info(self, project_token=None):
        empty = {"data_root": str(paths.SCRIPT_DIR), "shared_path": str(paths.MEMORY_LINK),
                 "shared_status": "missing", "tracked": False, "git_status": "untracked",
                 "changed_paths": [], "entries": [], "project": None, "locations": [],
                 "actions": {action: {"allowed": False, "reason": "請先設定資料庫"}
                             for action in ("enable", "disable", "adopt", "release", "push")}}
        if not self._lock.acquire(blocking=False):
            return {**empty, **outcome(1, "另一個動作正在執行", "BUSY")}
        try:
            if paths.CONFIG_ERROR or not (paths.SCRIPT_DIR / "claude").is_dir():
                return {**empty, **outcome(1, "請先設定資料庫", "NOT_CONFIGURED")}
            project = self._project_path(project_token) if project_token is not None else None
            state = memory.inspect(project or paths.HOME)
            entries = []
            for tool, path in (("claude", memory.live_rules_path()),
                               ("codex", memory.codex_rules_path()),
                               ("agy", memory.agy_rules_path())):
                entry = memory.entry_status(path)
                entries.append({"tool": tool, "path": str(path),
                                "status": entry["status"], "reason": entry["reason"],
                                "cli_installed": bool(shutil.which(tool))})
            from .memory_plan import plan

            actions = {}
            for action in ("enable", "disable", "adopt", "release"):
                if action in ("adopt", "release") and project is None:
                    actions[action] = {"allowed": False, "reason": "請先選擇本機專案"}
                    continue
                try:
                    candidate = plan(action, project if action in ("adopt", "release") else None)
                    actions[action] = {"allowed": bool(candidate.changes),
                                       "reason": "" if candidate.changes else "已一致"}
                except (OSError, RuntimeError, ValueError) as exc:
                    actions[action] = {"allowed": False, "reason": str(exc)}
            actions["push"] = {"allowed": state.changes is not None,
                               "reason": "" if state.changes is not None else "記憶尚未納入 Git"}
            locations = []
            self._locations = {}
            candidates = [("全域記憶", state.directory)]
            if project is not None:
                candidates += [("專案記憶", state.project_dir),
                               ("專案日誌", memory.project_journal_dir(state.project))]
            for label, location in candidates:
                token = secrets.token_urlsafe(24)
                self._locations[token] = (location, str(paths.SCRIPT_DIR), project_token)
                locations.append({"label": label, "path": str(location), "token": token})
            return {**empty, **outcome(), "shared_status": state.link if state.link in ("ok", "missing") else "conflict",
                    "tracked": state.changes is not None,
                    "git_status": "untracked" if state.changes is None else "dirty" if state.changes else "clean",
                    "changed_paths": state.changes or [], "entries": entries, "actions": actions,
                    "locations": locations,
                    "project": {"root": str(project), "key": state.project.key,
                                "stable": state.project.stable, "memory_path": str(state.project_dir),
                                "journal_path": str(memory.project_journal_dir(state.project)),
                                "journal_status": state.journal, "remember_installed": state.remember}
                    if project else None}
        except (OSError, RuntimeError, ValueError) as exc:
            return {**empty, **failure(exc)}
        finally:
            self._lock.release()

    def open_memory_location(self, token):
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            if not isinstance(token, str) or token not in self._locations:
                raise ValueError("無效的記憶位置")
            location, repo, project_token = self._locations[token]
            if str(paths.SCRIPT_DIR) != repo:
                raise StalePreview("資料庫已切換，請重新整理")
            if project_token is not None:
                self._project_path(project_token)
            memory.assert_plain_path(location, directory=True)
            if not location.is_dir():
                raise FileNotFoundError(f"尚未建立資料夾：{location}")
            command = "explorer" if sys.platform == "win32" else "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([command, str(location)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return outcome(output=f"已開啟 {location}")
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            self._lock.release()

    def _preview(self, kind, tool=None, category=None, action=None, project_token=None):
        empty = {"token": "", "needs_confirmation": False, "scope": {}, "changes": [], "warnings": []}
        if not self._lock.acquire(blocking=False):
            return {**empty, **outcome(1, "另一個動作正在執行", "BUSY")}
        try:
            self._discard_previews()
            if paths.CONFIG_ERROR or not (paths.SCRIPT_DIR / "claude").is_dir():
                return {**empty, **outcome(1, "請先設定資料庫", "NOT_CONFIGURED")}
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                if kind == "apply":
                    from .applyplan import plan

                    if tool != "all" and tool not in paths.ALL_TOOLS:
                        raise ValueError("無效的工具")
                    candidate = plan(list(paths.ALL_TOOLS) if tool == "all" else [tool], category)
                    scope = {"tool": tool, "category": category}
                    identity = candidate.identity
                else:
                    from .memory_plan import plan

                    if action in ("adopt", "release"):
                        project = self._project_path(project_token)
                    else:
                        if project_token is not None:
                            raise ValueError("此操作不接受專案參數")
                        project = None
                    candidate = plan(action, project)
                    scope = {"action": action, "project_key": memory.project_key(project).key if project else None}
                    identity = review.fingerprint(candidate.relevant_paths,
                                                  [candidate.relevant_values, review.git_state(paths.SCRIPT_DIR)])
            if not candidate.changes:
                if kind == "apply":
                    candidate.close()
                return {**empty, **outcome(output="已一致"), "scope": scope}
            token = secrets.token_urlsafe(24)
            self._pending = {"kind": kind, "token": token, "plan": candidate,
                             "identity": identity, "project_token": project_token,
                             "repo": str(paths.SCRIPT_DIR)}
            output = "\n".join(f"{change['operation']} {change['destination']} — {change['reason']}"
                               for change in candidate.changes)
            output += "\n" + "\n".join(candidate.warnings)
            return {**empty, **outcome(output=output), "token": token, "needs_confirmation": True,
                    "scope": scope, "changes": candidate.changes, "warnings": candidate.warnings}
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            return {**empty, **failure(exc)}
        finally:
            self._lock.release()

    def preview_apply(self, tool, category):
        return self._preview("apply", tool=tool, category=category)

    def preview_memory(self, action, project_token=None):
        return self._preview("memory", action=action, project_token=project_token)

    def _confirm(self, kind, token):
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        pending = None
        try:
            expected = self._pending
            if (not isinstance(token, str) or not expected or expected["kind"] != kind
                    or not secrets.compare_digest(token, expected["token"])):
                raise StalePreview("預覽已失效，請重新預覽")
            pending = expected
            self._pending = None
            if str(paths.SCRIPT_DIR) != pending["repo"]:
                raise StalePreview("資料庫已切換，請重新預覽")
            if pending["project_token"] is not None:
                self._project_path(pending["project_token"])
            candidate = pending["plan"]
            with apply_lock(timeout=0):
                if kind == "apply":
                    from .applyplan import execute

                    backup = execute(candidate)
                    return outcome(output="套用完成", backup=backup)
                from .commands.memory import execute
                from .memory_plan import plan

                fresh = plan(candidate.action, candidate.project)
                identity = review.fingerprint(fresh.relevant_paths,
                                              [fresh.relevant_values, review.git_state(paths.SCRIPT_DIR)])
                if identity != pending["identity"] or fresh.changes != candidate.changes:
                    raise StalePreview("內容在預覽後已有變動，請重新預覽")
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                    result = execute(candidate.action, candidate.project, lock_held=True)
                return outcome(result.code, buffer.getvalue(), backup=result.backup_path,
                               recovery=result.recovery_required)
        except TimeoutError as exc:
            # Acquiring the backend lock did not start a mutation.
            if pending:
                self._pending = pending
                pending = None
            return failure(exc)
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            if pending and pending["kind"] == "apply":
                pending["plan"].close()
            self._lock.release()

    def confirm_apply(self, token):
        return self._confirm("apply", token)

    def confirm_memory(self, token):
        return self._confirm("memory", token)
