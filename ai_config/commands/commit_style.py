"""acg commit-style: install or remove this machine's commit subject check."""

from ..console import log_error, log_header, log_info, log_success
from ..paths import ENTRYPOINT

USAGE = f"Usage: {ENTRYPOINT} commit-style <status|enable|disable>"


def run_commit_style(args: list[str]) -> int:
    from .. import commit_style

    action = args[0] if args else "status"
    if len(args) > 1 or action not in {"status", "enable", "disable"}:
        log_error(USAGE)
        return 1
    try:
        if action == "status":
            state = commit_style.status()
        else:
            log_header(f"{action.capitalize()} commit style check")
            state = commit_style.configure(action == "enable")
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    if state["installed"]:
        log_success("commit 主旨檢查已啟用")
        log_info("不合慣例的主旨會被擋下並說明原因;開新會話後生效")
    else:
        log_info("commit 主旨檢查未啟用")
    return 0
