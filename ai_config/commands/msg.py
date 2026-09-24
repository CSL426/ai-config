"""`acg msg`: talk to other live agent sessions by name."""

from .. import messaging
from ..console import log_error, log_info, log_success
from ..paths import ENTRYPOINT

_USAGE = (
    "Usage: {entry} msg list | send <名稱或 id> <訊息> [--wait [秒]]"
)
_DEFAULT_WAIT = 300.0


def _list() -> int:
    peers = messaging.list_peers()
    if not peers:
        log_info("沒有找到在線上、收得到訊息的 session")
        log_info("Codex 要用 codex --remote unix:// 開,才會掛上 daemon")
        return 0
    for peer in peers:
        name = peer.name or "(未命名)"
        print(f"  {peer.tool} {peer.account:<8} {name}  [{peer.id[:13]}]  {peer.status}  {peer.cwd}")
    return 0


def _send(args: list) -> int:
    wait = 0.0
    rest = list(args)
    if "--wait" in rest:
        index = rest.index("--wait")
        rest.pop(index)
        wait = _DEFAULT_WAIT
        if index < len(rest):
            try:
                wait = float(rest[index])
                rest.pop(index)
            except ValueError:
                pass
    if len(rest) != 2:
        log_error(_USAGE.format(entry=ENTRYPOINT))
        return 1
    target, text = rest
    peer, reply = messaging.send(target, text, wait=wait)
    log_success(f"已送到 {peer.label}({peer.tool} {peer.account})")
    if wait:
        print()
        print(reply or "(對方這一輪沒有文字回覆)")
    return 0


def run_msg(args: list) -> int:
    action = args[0] if args else "list"
    try:
        if action == "list" and len(args) <= 1:
            return _list()
        if action == "send":
            return _send(args[1:])
    except messaging.MessagingError as exc:
        log_error(str(exc))
        return 1
    log_error(_USAGE.format(entry=ENTRYPOINT))
    return 1
