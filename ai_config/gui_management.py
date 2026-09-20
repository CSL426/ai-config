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


def _keepalive_state() -> dict:
    """Never let the scheduler take down the whole memory page."""
    blank = {"installed": False, "times": [], "model": "", "ccs": "", "recent": []}
    try:
        from . import keepalive

        settings = keepalive.load()
        return {"installed": keepalive.installed(), "times": list(settings.times),
                "model": settings.model, "ccs": keepalive.existing_ccs(),
                "recent": keepalive.last_runs()}
    except (ImportError, OSError, RuntimeError, ValueError):
        return blank


def _autopush_state() -> dict:
    """Never let a scheduler hiccup take down the whole memory page."""
    blank = {"installed": False, "last_push": "", "reason": "",
             "slot": "", "host": "", "others": []}
    try:
        from . import autopush, schedule_table

        state = autopush.status()
        host = schedule_table.host_name()
        table = schedule_table.load()
        mine = table.hosts.get(host)
        others = [
            {"host": name, "slot": str(slot)}
            for name, slot in sorted(table.hosts.items())
            if name != host
        ]
        return {"installed": state["installed"], "last_push": state["last_push"],
                "reason": state["reason"], "host": host,
                "slot": str(mine) if mine else "", "others": others}
    except (ImportError, OSError, RuntimeError, ValueError):
        # 排程查詢壞掉不該讓整個記憶頁打不開
        return blank


def outcome(code=0, output="", error=None, backup=None, recovery=False):
    return {"code": code, "output": output, "error": error,
            "backup_path": str(backup) if backup else None,
            "recovery_required": recovery}


def _handoff_reminder_state() -> dict:
    try:
        from . import handoff_reminder

        return {**handoff_reminder.status(), "reason": ""}
    except (OSError, RuntimeError, ValueError) as exc:
        return {"enabled": False, "threshold": 70, "installed": False,
                "reason": str(exc)}


def _remember_hosts_state() -> dict:
    from . import remember_hosts

    result = {}
    for host in remember_hosts.HOSTS:
        try:
            found = remember_hosts.state(host)
            result[host] = {"available": remember_hosts.available(host),
                            "installed": found.installed, "version": found.version,
                            "trusted": found.trusted, "detail": found.detail}
        except (OSError, RuntimeError, ValueError) as exc:
            result[host] = {"available": remember_hosts.available(host),
                            "installed": False, "version": "", "trusted": None,
                            "detail": str(exc)}
    return result


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

    def select_skill_directory(self):
        result = {"cancelled": False, "path": None}
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
            # Keep links visible so the CLI can reject unsafe source paths.
            chosen = Path(selected[0])
            if not chosen.is_absolute() or not chosen.is_dir():
                raise ValueError("請選擇技能資料夾")
            return {**result, **outcome(), "path": str(chosen)}
        except Exception as exc:  # noqa: BLE001 - native chooser errors vary by OS
            return {**result, **failure(exc)}
        finally:
            self._lock.release()

    def add_skill(self, source):
        if (not isinstance(source, str) or not source.strip()
                or "\0" in source or not Path(source).is_absolute()):
            return outcome(1, "請選擇有效的技能資料夾完整路徑", "INVALID_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            self._ensure_configured()
            self._discard_previews()
            return self._run_captured(["skill", "add", source])
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            self._lock.release()

    def memory_info(self, project_token=None):
        empty = {"data_root": str(paths.SCRIPT_DIR), "shared_path": str(paths.MEMORY_LINK),
                 "shared_status": "missing", "tracked": False, "git_status": "untracked",
                 "index_unlisted": [], "index_dangling": [], "secret_notes": [],
                 "keepalive": {"installed": False, "times": [], "model": "",
                               "ccs": "", "recent": []},
                 "autopush": {"installed": False, "last_push": "", "reason": "",
                              "slot": "", "host": "", "others": []},
                 "handoff_reminder": None, "remember_hosts": None,
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
                    "index_unlisted": state.index_unlisted,
                    "index_dangling": state.index_dangling,
                    "secret_notes": state.secret_notes,
                    "autopush": _autopush_state(),
                    "keepalive": _keepalive_state(),
                    "handoff_reminder": _handoff_reminder_state(),
                    "remember_hosts": _remember_hosts_state(),
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

    def set_handoff_reminder(self, enabled, threshold=70):
        """Configure the Claude Code reminder under the GUI action lock."""
        if (not isinstance(enabled, bool) or type(threshold) is not int
                or not 1 <= threshold <= 99):
            return outcome(1, "提醒門檻必須是 1 到 99 的整數", "INVALID_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            from . import handoff_reminder

            self._ensure_configured()
            self._discard_previews()
            state = handoff_reminder.configure(enabled, threshold)
            message = (f"已啟用 Claude Code 交接提醒，門檻 {state['threshold']}%。"
                       if state["enabled"] else "已停用 Claude Code 交接提醒。")
            return {**outcome(output=message), "handoff_reminder": state}
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            self._lock.release()

    def set_remember_host(self, host, enabled):
        """Install or remove remember's capture on Codex or Antigravity."""
        from . import remember_hosts

        if host not in remember_hosts.HOSTS or not isinstance(enabled, bool):
            return outcome(1, "host 必須是 codex 或 agy", "INVALID_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            self._ensure_configured()
            self._discard_previews()
            lines = (remember_hosts.install(host) if enabled
                     else remember_hosts.remove(host))
            message = "\n".join(lines) or "沒有需要改的"
            return {**outcome(output=message), "remember_hosts": _remember_hosts_state()}
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            self._lock.release()

    def set_autopush(self, wanted):
        """Schedule or unschedule the daily memory save."""
        if not isinstance(wanted, bool):
            return outcome(1, "參數不正確", "INVALID_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            from . import autopush

            self._ensure_configured()
            lines = autopush.enable() if wanted else autopush.disable()
            return {**outcome(), "output": "\n".join(lines)}
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            self._lock.release()

    def set_keepalive(self, wanted, times=None):
        """Schedule or unschedule the calls that anchor the usage window."""
        if not isinstance(wanted, bool):
            return outcome(1, "參數不正確", "INVALID_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            from . import keepalive

            if not wanted:
                code, lines = keepalive.disable()
            else:
                chosen = tuple(times) if isinstance(times, list) else ()
                code, lines = keepalive.enable(chosen)
            message = "\n".join(lines)
            if code != 0:
                return {**outcome(code, message, "KEEPALIVE_REFUSED"),
                        "output": message}
            return {**outcome(), "output": message}
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
        finally:
            self._lock.release()

    def set_autopush_slot(self, clock):
        """Move this machine to a chosen time and rebuild its schedule."""
        if not isinstance(clock, str) or ":" not in clock:
            return outcome(1, "時間格式要像 04:30", "INVALID_ARGUMENT")
        hour, _, minute = clock.partition(":")
        try:
            hour, minute = int(hour), int(minute)
        except ValueError:
            return outcome(1, "時間格式要像 04:30", "INVALID_ARGUMENT")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return outcome(1, "時間要在 00:00 到 23:59 之間", "INVALID_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            return outcome(1, "另一個動作正在執行", "BUSY")
        try:
            from . import autopush, schedule_table

            self._ensure_configured()
            schedule_table.record(
                schedule_table.host_name(), schedule_table.Slot(hour, minute)
            )
            lines = autopush.enable(hour)
            return {**outcome(), "output": "\n".join(lines)}
        except (OSError, RuntimeError, ValueError) as exc:
            return failure(exc)
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
