"""`acg msg`: talk to other live agent sessions by name."""

from .. import messaging
from ..console import log_error, log_info, log_success
from ..paths import ENTRYPOINT

_USAGE = (
    "Usage: {entry} msg list | setup | send <名稱或 id> <訊息> [--wait [秒]] [--from <我的名稱>]"
)
_DEFAULT_WAIT = 300.0


def _list() -> int:
    peers = messaging.list_peers()
    if not peers:
        log_info("沒有找到在線上的 session")
        log_info("Codex 要用 codex --remote unix:// 開,才會掛上 daemon")
        return 0
    for peer in peers:
        mark = "○" if peer.unreachable else "●"
        tool = f"{peer.tool} {peer.account}".strip()
        name = peer.name or "(未命名)"
        where = f"  {peer.cwd}" if peer.cwd else ""
        print(f"  {mark} {tool:<10} {name}  [{peer.id[:13]}]  {peer.status}{where}")
    if any(peer.unreachable for peer in peers):
        log_info("● 收得到訊息;○ 目前收不到,送給它會說明原因")
    return 0


def _send(args: list) -> int:
    wait = 0.0
    sender = ""
    rest = list(args)
    if "--from" in rest:
        index = rest.index("--from")
        if index + 1 >= len(rest):
            log_error(_USAGE.format(entry=ENTRYPOINT))
            return 1
        sender = rest[index + 1]
        del rest[index:index + 2]
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
    peer, reply = messaging.send(target, text, wait=wait, sender=sender)
    log_success(f"已送到 {peer.label}({peer.tool} {peer.account})")
    if wait:
        print()
        print(reply or "(對方這一輪沒有文字回覆)")
    return 0


def _setup() -> int:
    path = messaging.write_channel_config()
    log_success(f"已寫入 Claude channel 設定:{path}")
    log_info("把下面這段加進 ~/.bashrc,之後照常打 claude,別的 session 就能傳話進來;")
    log_info("每次開 Claude 會多一個 development channel 警告,按 Enter 即可(官方規定,關不掉)")
    print()
    print(messaging.shell_function())
    return 0


def run_msg(args: list) -> int:
    action = args[0] if args else "list"
    try:
        if action == "list" and len(args) <= 1:
            return _list()
        if action == "setup" and len(args) == 1:
            return _setup()
        if action == "send":
            return _send(args[1:])
    except messaging.MessagingError as exc:
        log_error(str(exc))
        return 1
    log_error(_USAGE.format(entry=ENTRYPOINT))
    return 1
