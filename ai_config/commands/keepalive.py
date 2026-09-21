"""`acg keepalive`: anchor this machine's usage windows."""

from .. import keepalive
from ..console import log_error, log_header, log_info, log_success
from ..paths import ENTRYPOINT

_USAGE = (
    "Usage: {entry} keepalive "
    "[status|enable [HH:MM ...]|disable|send] [claude|codex|agy]"
)


def _split_tool(args: list) -> tuple:
    """Pull a tool name off the end; everything before it is the action's own."""
    rest = list(args)
    tool = keepalive.DEFAULT_TOOL
    for index, value in enumerate(rest):
        if value in keepalive.TOOLS:
            tool = rest.pop(index)
            break
    return rest, tool


def _report(tool: str) -> None:
    settings = keepalive.load(tool)
    if keepalive.installed(tool):
        log_success(f"{tool}:已啟用 {', '.join(settings.times)}")
    else:
        log_info(f"{tool}:未啟用")
    recent = keepalive.last_runs(tool=tool)
    if recent:
        print(f"    {recent[-1]}")


def _status(tool: "str | None") -> int:
    log_header("Keepalive")
    for name in ([tool] if tool else keepalive.TOOLS):
        _report(name)
    if not tool:
        log_info(f"啟用:{ENTRYPOINT} keepalive enable [HH:MM ...] [工具]")
        log_info("每個工具的用量視窗各自獨立,時間也各自設定")
    found = keepalive.existing_ccs()
    if found:
        log_info(f"注意:claude-scheduler 的排程也還在({found})")
    return 0


def run_keepalive(args: list) -> int:
    rest, tool = _split_tool(args)
    action = rest[0] if rest else "status"
    named = any(a in keepalive.TOOLS for a in args)

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
                    f"(可用:{', '.join(keepalive.TOOLS)})"
                )
            code, lines = keepalive.enable(times, replace_ccs=replace, tool=tool)
            for line in lines:
                (log_info if code == 0 else log_error)(line)
            return code

        if action == "disable":
            code, lines = keepalive.disable(tool)
            for line in lines:
                log_info(line)
            return code

        if action == "send":
            return keepalive.send(tool)
    except ValueError as exc:
        log_error(str(exc))
        return 1

    log_error(f"Unknown keepalive action: {action}")
    log_info(_USAGE.format(entry=ENTRYPOINT))
    return 1
