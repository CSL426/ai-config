"""`acg keepalive`: anchor this machine's Claude usage window."""

from .. import keepalive
from ..console import log_error, log_header, log_info, log_success
from ..paths import ENTRYPOINT


def _status() -> int:
    settings = keepalive.load()
    log_header("Keepalive")
    if keepalive.installed():
        log_success(f"已啟用:{', '.join(settings.times)}")
    else:
        log_info("未啟用")
        log_info(f"啟用:{ENTRYPOINT} keepalive enable [HH:MM ...]")
    log_info(f"模型:{settings.model}")
    found = keepalive.existing_ccs()
    if found:
        log_info(f"注意:claude-scheduler 的排程也還在({found})")
    recent = keepalive.last_runs()
    if recent:
        log_info("最近幾次:")
        for line in recent:
            print(f"    {line}")
    return 0


def run_keepalive(args: list) -> int:
    action = args[0] if args else "status"

    if action in {"status", "--help", "-h"} and len(args) <= 1:
        if action != "status":
            log_info(
                f"Usage: {ENTRYPOINT} keepalive "
                "[status|enable [HH:MM ...]|disable|send]"
            )
            return 0
        return _status()

    if action == "enable":
        rest = list(args[1:])
        replace = "--replace-ccs" in rest
        times = tuple(a for a in rest if a != "--replace-ccs")
        code, lines = keepalive.enable(times, replace_ccs=replace)
        for line in lines:
            (log_info if code == 0 else log_error)(line)
        return code

    if action == "disable":
        code, lines = keepalive.disable()
        for line in lines:
            log_info(line)
        return code

    if action == "send":
        return keepalive.send()

    log_error(f"Unknown keepalive action: {action}")
    log_info(
        f"Usage: {ENTRYPOINT} keepalive [status|enable [HH:MM ...]|disable|send]"
    )
    return 1
