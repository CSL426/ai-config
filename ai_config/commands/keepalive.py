"""`acg keepalive`: anchor this machine's usage windows."""

from .. import (
    keepalive_runner,
    keepalive_scheduler,
    keepalive_settings,
    keepalive_window,
)
from ..console import log_error, log_header, log_info, log_success, log_warn
from ..paths import ENTRYPOINT

_USAGE = (
    "Usage: {entry} keepalive "
    "[status|enable [HH:MM ...]|disable|send] [claude|codex|agy]"
)


def _split_tool(args: list) -> tuple:
    """Pull a tool name off the end; everything before it is the action's own."""
    rest = list(args)
    tool = keepalive_settings.DEFAULT_TOOL
    for index, value in enumerate(rest):
        if value in keepalive_settings.TOOLS:
            tool = rest.pop(index)
            break
    return rest, tool


def _report(tool: str) -> None:
    settings = keepalive_settings.load(tool)
    if keepalive_scheduler.installed(tool):
        log_success(f"{tool}:已啟用 {', '.join(settings.times)}")
    else:
        log_info(f"{tool}:未啟用")
    accounts = keepalive_window.last_by_account(tool)
    if accounts:
        # 一個工具有好幾個帳號時,每個帳號的視窗各自獨立,結果要分開看
        for home, line in accounts.items():
            print(f"    {home}: {line}")
    else:
        recent = keepalive_window.last_runs(tool=tool)
        if recent:
            print(f"    {recent[-1]}")
    if tool == keepalive_settings.DEFAULT_TOOL:
        _report_window(settings.times)


def _report_window(times) -> None:
    """Where the window actually is, next to where the schedule meant it to be."""
    from datetime import datetime

    window = keepalive_window.current_window()
    if window is None:
        return
    start, reset = window
    print(f"    目前視窗 {start:%H:%M}–{reset:%H:%M}")
    expected = keepalive_window.drift(start, times, datetime.now().astimezone())
    if expected:
        # 起點不是排程時間,代表那一次呼叫沒錨定到;通常是更早有別的用量
        # (別台機器、網頁、手機)先開了視窗
        log_warn(
            f"視窗不是從排程的 {expected} 開始:那次呼叫落在別人開的視窗裡,"
            "同帳號在更早的時間有其他用量"
        )


def _status(tool: "str | None") -> int:
    log_header("Keepalive")
    for name in ([tool] if tool else keepalive_settings.TOOLS):
        _report(name)
    if not tool:
        log_info(f"啟用:{ENTRYPOINT} keepalive enable [HH:MM ...] [工具]")
        log_info("每個工具的用量視窗各自獨立,時間也各自設定")
    found = keepalive_scheduler.existing_ccs()
    if found:
        log_info(f"注意:claude-scheduler 的排程也還在({found})")
    return 0


def run_keepalive(args: list) -> int:
    rest, tool = _split_tool(args)
    action = rest[0] if rest else "status"
    named = any(a in keepalive_settings.TOOLS for a in args)

    if action in {"--help", "-h"}:
        log_info(_USAGE.format(entry=ENTRYPOINT))
        return 0

    try:
        if action == "status":
            return _status(tool if named else None)

        if action == "enable":
            options = rest[1:]
            replace = "--replace-ccs" in options
            times = tuple(a for a in options if a != "--replace-ccs")
            # 看起來像工具名而不是時間的,多半是打錯工具,說清楚比報時間格式有用
            unclocked = [a for a in times if ":" not in a]
            if unclocked:
                raise ValueError(
                    f"不認得這個工具:{unclocked[0]}"
                    f"(可用:{', '.join(keepalive_settings.TOOLS)})"
                )
            code, lines = keepalive_scheduler.enable(times, replace_ccs=replace, tool=tool)
            for line in lines:
                (log_info if code == 0 else log_error)(line)
            return code

        if action == "disable":
            code, lines = keepalive_scheduler.disable(tool)
            for line in lines:
                log_info(line)
            return code

        if action == "send":
            return keepalive_runner.send(tool)
    except ValueError as exc:
        log_error(str(exc))
        return 1

    log_error(f"Unknown keepalive action: {action}")
    log_info(_USAGE.format(entry=ENTRYPOINT))
    return 1
