"""Claude context reminders; settings and usage remain machine-local."""

import base64
import copy
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import memory
from .locking import apply_lock
from .paths import NATIVE_WINDOWS

STATUS_COMMAND = "__handoff-statusline"
HOOK_COMMAND = "__handoff-reminder"
MARKER = "acg：交接用量提醒"
EVENTS = ("UserPromptSubmit", "PostToolUse", "PreCompact", "SessionEnd")
DEFAULT_THRESHOLD = 70
MAX_AGE = 300
MAX_INPUT = 4 * 1024 * 1024
_WRAPPER = re.compile(
    r"(?:^| )__handoff-statusline ([A-Za-z0-9_=-]+) ([0-9]+)$"
)


def _threshold(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 99:
        raise ValueError("提醒門檻必須是 1 到 99 的整數百分比")
    return value


def _decode(value: str) -> dict | None:
    original = json.loads(base64.urlsafe_b64decode(value).decode("utf-8"))
    if original is not None and (
        not isinstance(original, dict)
        or original.get("type") != "command"
        or not isinstance(original.get("command"), str)
    ):
        raise ValueError("無法辨識原本的 status line")
    return original


def _wrapper(document: dict) -> tuple[dict | None, int] | None:
    line = document.get("statusLine")
    if not isinstance(line, dict) or not isinstance(line.get("command"), str):
        return None
    match = _WRAPPER.search(line["command"])
    if match is None:
        return None
    return _decode(match[1]), _threshold(int(match[2]))


def _owned(hook: object) -> bool:
    return isinstance(hook, dict) and hook.get("statusMessage") == MARKER


def without_settings(document: dict) -> dict:
    """Recover the portable status line and remove only our hook entries."""
    result = copy.deepcopy(document)
    wrapped = _wrapper(result)
    if wrapped is not None:
        if wrapped[0] is None:
            result.pop("statusLine", None)
        else:
            result["statusLine"] = wrapped[0]
    events = result.get("hooks", {})
    if not isinstance(events, dict):
        return result
    for event in EVENTS:
        rows = events.get(event)
        if not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                kept.append(row)
                continue
            hooks = [hook for hook in row["hooks"] if not _owned(hook)]
            if hooks == row["hooks"]:
                kept.append(row)
            elif hooks:
                kept.append({**row, "hooks": hooks})
        if kept:
            events[event] = kept
        else:
            events.pop(event, None)
    if not events and result.get("hooks") != document.get("hooks"):
        result.pop("hooks", None)
    return result


def _encode(original: dict | None) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(original, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


def preserve_settings(source: dict, target: dict) -> dict:
    """Keep this machine's executable while applying the new shared display."""
    result = without_settings(source)
    wrapped = _wrapper(target)
    if wrapped is not None:
        line = copy.deepcopy(target["statusLine"])
        match = _WRAPPER.search(line["command"])
        line["command"] = (
            line["command"][:match.start(1)]
            + _encode(result.get("statusLine"))
            + f" {wrapped[1]}"
        )
        original = result.get("statusLine") or {}
        result["statusLine"] = {**original, "type": "command", "command": line["command"]}
    events = target.get("hooks", {})
    if not isinstance(events, dict):
        return result
    for event in EVENTS:
        rows = events.get(event, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                continue
            owned = [hook for hook in row["hooks"] if _owned(hook)]
            if owned:
                result.setdefault("hooks", {}).setdefault(event, []).append(
                    {**row, "hooks": copy.deepcopy(owned)}
                )
    return result


def _settings() -> dict:
    path = memory.CLAUDE_HOME / "settings.json"
    memory.assert_plain_path(path, directory=False)
    value = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError("Claude settings.json 必須是物件")  # noqa: TRY004
    return value


def _has_reminder_hook(rows: object) -> bool:
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(row, dict)
        and isinstance(row.get("hooks"), list)
        and any(_owned(hook) for hook in row["hooks"])
        for row in rows
    )


def _status(document: dict) -> dict:
    wrapped = _wrapper(document)
    events = document.get("hooks", {})
    installed = isinstance(events, dict) and all(
        _has_reminder_hook(events.get(event)) for event in EVENTS
    )
    return {
        "enabled": wrapped is not None,
        "threshold": wrapped[1] if wrapped else DEFAULT_THRESHOLD,
        "installed": wrapped is not None and installed,
    }


def status() -> dict:
    return _status(_settings())


def configure(enabled: bool, threshold: int = DEFAULT_THRESHOLD) -> dict:
    from .commands.memory import _backup

    if type(enabled) is not bool:
        raise ValueError("提醒開關必須是布林值")
    threshold = _threshold(threshold)
    with apply_lock():
        document = _settings()
        result = without_settings(document)
        if enabled:
            original = result.get("statusLine")
            _decode(_encode(original))
            executable = sys.executable.replace("\\", "/")
            argv = [executable]
            if not getattr(sys, "frozen", False):
                argv += ["-m", "ai_config"]
            result["statusLine"] = {
                **(original or {}), "type": "command",
                "command": _shell_command(argv + [
                    STATUS_COMMAND, _encode(original), str(threshold),
                ]),
            }
            events = result.setdefault("hooks", {})
            if not isinstance(events, dict):
                raise ValueError("Claude hooks 必須是物件")
            for event in EVENTS:
                rows = events.setdefault(event, [])
                if not isinstance(rows, list):
                    raise ValueError(f"Claude {event} hooks 必須是陣列")  # noqa: TRY004
                rows.append({"hooks": [{
                    "type": "command", "command": sys.executable,
                    "args": argv[1:] + [HOOK_COMMAND],
                    "statusMessage": MARKER, "timeout": 5,
                }]})
        if result != document:
            path = memory.CLAUDE_HOME / "settings.json"
            _backup([path])
            memory._write_text_atomic(
                path, json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            )
        return _status(result)


def _session_path(payload: dict):
    session = payload.get("session_id")
    cwd = payload.get("cwd")
    if (
        not isinstance(session, str) or not session or len(session) > 512
        or not isinstance(cwd, str) or not os.path.isabs(cwd)
    ):
        raise ValueError("Missing session identity")
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()
    path = memory.CLAUDE_HOME / ".acg-handoff-reminder" / f"{key}.json"
    memory.assert_plain_path(path, directory=False)
    return path


def _read_state(path) -> dict:
    if not path.exists():
        return {}
    if path.stat().st_size > 8192:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _percentage(payload: dict) -> float | None:
    window = payload.get("context_window")
    if not isinstance(window, dict):
        return None
    # Claude clears usage after compaction until the next API response.
    if "current_usage" in window and window["current_usage"] is None:
        return None
    value = window.get("used_percentage")
    if "used_percentage" not in window:
        remaining = window.get("remaining_percentage")
        if type(remaining) in (int, float):
            value = 100 - remaining
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value) if 0 <= value <= 100 else None


def record(payload: dict, threshold: int) -> float | None:
    threshold = _threshold(threshold)
    with apply_lock(timeout=0.2):
        path = _session_path(payload)
        previous = _read_state(path)
        used = _percentage(payload)
        state = {
            "cwd": payload["cwd"], "used": used, "time": time.time(),
            "threshold": threshold,
            "notified": bool(
                previous.get("notified")
                and previous.get("threshold") == threshold
            ),
        }
        memory._write_text_atomic(path, json.dumps(state))
    return used


def reminder(payload: dict) -> dict | None:
    event = payload.get("hook_event_name")
    # Subagent tool hooks can carry the parent's session id. The main window's
    # reading belongs to the parent, so a child must not consume its reminder.
    if event not in EVENTS or payload.get("agent_id"):
        return None
    with apply_lock(timeout=0.2):
        path = _session_path(payload)
        if event in {"PreCompact", "SessionEnd"}:
            if path.exists():
                memory._unlink_file(path)
            return None
        settings = status()
        if not settings["installed"]:
            return None
        state = _read_state(path)
        used = state.get("used")
        timestamp = state.get("time")
        if state.get("cwd") != payload["cwd"] or state.get("notified"):
            return None
        if state.get("threshold") != settings["threshold"]:
            return None
        if (
            type(used) not in (int, float)
            or not math.isfinite(used)
            or not settings["threshold"] <= used <= 100
        ):
            return None
        if (
            type(timestamp) not in (int, float)
            or not 0 <= time.time() - timestamp <= MAX_AGE
        ):
            return None
        state["notified"] = True
        memory._write_text_atomic(path, json.dumps(state))
        return {"hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": (
                f"本 session 最近的 context 使用率為 {used:g}%，"
                f"已達交接提醒門檻 {settings['threshold']}%。"
                "建議提醒使用者準備 /acg:handoff，交接應包含已完成事項、"
                "目前阻礙與下一步。這只是提醒，尚未建立或更新交接；"
                "是否寫入由使用者決定。"
            ),
        }}


def run_hook(args: list[str]) -> int:
    try:
        if args:
            return 0
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            return 0
        payload = json.loads(raw)
        if isinstance(payload, dict):
            output = reminder(payload)
            if output:
                print(json.dumps(output, ensure_ascii=False))
    except (OSError, RuntimeError, ValueError, TypeError):
        pass
    return 0


def run_statusline(args: list[str]) -> int:
    if len(args) != 2:
        return 0
    try:
        original = _decode(args[0])
        threshold = _threshold(int(args[1]))
    except (ValueError, TypeError):
        return 0
    raw = sys.stdin.read(MAX_INPUT + 1)
    used = None
    try:
        if len(raw) <= MAX_INPUT:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                used = record(payload, threshold)
    except (OSError, RuntimeError, ValueError, TypeError):
        pass
    should_remind = used is not None and used >= threshold
    if original:
        # Execute only the user's configured display, never transcript content.
        command = original["command"]
        try:
            result = subprocess.run(
                _shell_argv(command),
                input=raw, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=3,
                check=False,
            )
            sys.stdout.write(result.stdout)
            if should_remind and not result.stdout.endswith("\n"):
                print()
        except (OSError, subprocess.TimeoutExpired):
            pass
    if should_remind:
        print(f"Context {used:g}% · 建議準備 /acg:handoff")
    return 0


def _bash() -> str | None:
    configured = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if configured:
        return configured
    if NATIVE_WINDOWS:
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash")


def _shell_command(argv: list[str]) -> str:
    if NATIVE_WINDOWS and _bash() is None:
        # Keep the hidden command and its safe payload unquoted for recognition.
        prefix = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv[:-3])
        return prefix + " " + " ".join(argv[-3:])
    return shlex.join(argv)


def _shell_argv(command: str) -> list[str]:
    bash = _bash()
    if NATIVE_WINDOWS and bash is None:
        shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [shell, "-NoProfile", "-Command", command]
    return [bash or "sh", "-c", command]
