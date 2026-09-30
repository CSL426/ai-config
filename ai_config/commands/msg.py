"""`acg msg`: talk to other live agent sessions by name."""

import os

from .. import messaging
from ..console import log_error, log_info, log_success
from ..paths import ENTRYPOINT

_USAGE = (
    "Usage: {entry} msg list | setup | send <名稱、id 或 pid> <訊息> [--wait [秒]] [--from <我的名稱>]"
)
_DEFAULT_WAIT = 300.0


def _list() -> int:
    peers = messaging.list_peers()
    if not peers:
        log_info("沒有找到在線上的 session")
        log_info("Codex 要用 codex --remote unix:// 開,才會掛上 daemon")
        return 0
    shared = messaging.shared_ids(peers)
    for peer in peers:
        mark = "○" if peer.unreachable else "●"
        tool = f"{peer.tool} {peer.account}".strip()
        name = peer.name or "(未命名)"
        where = f"  {peer.cwd}" if peer.cwd else ""
        # 平常 id 就夠辨識;撞 id 時才補 pid,免得兩行長得一模一樣
        ident = f"{peer.id[:13]} pid {peer.pid}" if peer.id in shared and peer.pid else peer.id[:13]
        print(f"  {mark} {tool:<10} {name}  [{ident}]  {peer.status}{where}")
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
    profile = "PowerShell 的 $PROFILE" if os.name == "nt" else "~/.bashrc"
    log_info(f"把下面這段加進 {profile}(已經有舊的 acg msg 區塊就整段換掉),之後照常打 claude、codex,")
    log_info("別的 session 就能傳話進來。每次開 Claude 會多一個 development channel 警告,按 Enter 即可")
    log_info("(官方規定,關不掉)。自己已經有 codex 函式的(例如切換帳號),把 --remote 那段併進去,")
    log_info("不要兩個都留:後定義的會蓋掉先定義的")
    if os.name == "nt":
        # Windows 用戶端預設執行原則是 Restricted,$PROFILE 根本不會載入
        log_info("開新的 PowerShell 後函式沒生效的話,執行原則要允許本機腳本:")
        log_info("  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned")
    print()
    print(messaging.shell_functions())
    return 0


def run_msg(args: list) -> int:
    action = args[0] if args else "list"
    if action in {"--help", "-h"}:
        log_info(_USAGE.format(entry=ENTRYPOINT))
        return 0
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
