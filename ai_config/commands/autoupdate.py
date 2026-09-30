"""`acg autoupdate`: keep acg and the AI CLIs on this machine current."""

from .. import autoupdate
from ..console import log_error, log_header, log_info, log_success, log_warn
from ..paths import ENTRYPOINT

USAGE = "Usage: {entry} autoupdate [status|enable [HH:MM]|disable|run]"


def _local(stamp: str) -> str:
    from datetime import datetime

    try:
        return f"{datetime.fromisoformat(stamp).astimezone():%Y-%m-%d %H:%M}"
    except ValueError:
        return stamp


def _status() -> int:
    log_header("Auto-update")
    state = autoupdate.status()
    if state["installed"]:
        log_success(f"已啟用,每天 {state['time'] or '?'} 更新 acg、Claude Code、Codex、Antigravity")
    else:
        log_info("未啟用")
        log_info(f"啟用:{ENTRYPOINT} autoupdate enable [HH:MM](預設 05:30)")
    last = autoupdate.last_run()
    if last is None:
        log_info("還沒有執行紀錄")
        return 0
    print(f"  上次執行 {_local(last['when'])}")
    for step in last["steps"]:
        print(f"    {step.line()}")
    if any(not step.ok for step in last["steps"]):
        log_warn(f"有工具沒更新成功;處理後可用 {ENTRYPOINT} autoupdate run 立即重跑")
    return 0


def run_autoupdate(args: list) -> int:
    action = args[0] if args else "status"
    if action in {"--help", "-h"}:
        log_info(USAGE.format(entry=ENTRYPOINT))
        return 0
    try:
        if action == "status" and len(args) <= 1:
            return _status()
        if action == "enable" and len(args) <= 2:
            for line in autoupdate.enable(args[1] if len(args) == 2 else None):
                log_info(line)
            return 0
        if action == "disable" and len(args) == 1:
            for line in autoupdate.disable():
                log_info(line)
            return 0
        if action == "run" and len(args) == 1:
            return autoupdate.run()
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(USAGE.format(entry=ENTRYPOINT))
    return 1
