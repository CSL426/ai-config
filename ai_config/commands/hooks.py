"""acg hooks: what this machine can install into Claude Code, and what it has."""

from ..console import log_error, log_header, log_info, log_success
from ..paths import ENTRYPOINT

USAGE = f"Usage: {ENTRYPOINT} hooks <list|enable <名稱>|disable <名稱>>"


def run_hooks(args: list[str]) -> int:
    from .. import hooks

    action = args[0] if args else "list"
    rest = args[1:]
    try:
        if action == "list" and not rest:
            return _list()
        if action in {"enable", "disable"} and len(rest) == 1:
            hook = hooks.REGISTRY.get(rest[0])
            if hook is None:
                log_error(f"沒有這個 hook:{rest[0]}")
                log_info(f"可用的:{', '.join(hooks.REGISTRY)}")
                return 1
            log_header(f"{action.capitalize()} {hook.name}")
            on = hooks.configure(hook, action == "enable")
            if on:
                log_success(f"{hook.name} 已安裝;開新會話後生效")
            else:
                log_info(f"{hook.name} 已移除")
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(USAGE)
    return 1


def _list() -> int:
    from .. import hooks

    log_header("Claude Code hooks")
    for hook, on in hooks.states():
        mark = "✓" if on else "—"
        print(f"  {mark} {hook.name}")
        print(f"      {hook.summary}")
    log_info(
        "這些 hook 只裝在這台:它們指向本機的執行檔,所以不會同步到其他機器"
    )
    log_info(f"安裝:{ENTRYPOINT} hooks enable <名稱>")
    return 0
