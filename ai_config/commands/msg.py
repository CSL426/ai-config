"""`acg msg`: talk to other live agent sessions by name."""

import os
import shutil

from .. import messaging
from ..console import log_error, log_info, log_success
from ..paths import ENTRYPOINT

_USAGE = (
    "Usage: {entry} msg list | setup [--print] | send <名稱、id 或 pid> <訊息> [--wait [秒]] [--from <我的名稱>]"
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
        if peer.pane:
            ident += f" · herdr {peer.pane}"
        print(f"  {mark} {tool:<10} {name}  [{ident}]  {peer.status}{where}")
    if any(peer.unreachable for peer in peers):
        log_info("● 收得到訊息;○ 目前收不到,送給它會說明原因")
    if messaging.outside_herdr_agy(peers):
        _herdr_hint()
    return 0


def _herdr_hint() -> None:
    if shutil.which("herdr"):
        log_info("Antigravity 要在 herdr 的窗格裡開才收得到:先執行 herdr,再在裡面開 agy")
        return
    log_info("Antigravity 要在 herdr 的窗格裡開才收得到;herdr 沒裝,安裝方式:")
    if os.name == "nt":
        print('    powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 | iex"')
    else:
        print("    curl -fsSL https://herdr.dev/install.sh | sh")


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
    tool = f"{peer.tool} {peer.account}".strip()  # Claude 沒有帳號欄
    log_success(f"已送到 {peer.label}({tool})")
    if wait:
        print()
        print(reply or "(對方這一輪沒有文字回覆)")
    return 0


def _setup(print_only: bool = False) -> int:
    path = messaging.write_channel_config()
    log_success(f"已寫入 Claude channel 設定:{path}")
    if print_only:
        print(messaging.shell_block())
        return 0
    try:
        rc, skipped, changed = messaging.install_shell_block()
    except (OSError, messaging.MessagingError) as exc:
        log_error(f"沒辦法寫入 shell 設定:{exc};請自己把下面這段加進去")
        print(messaging.shell_block())
        return 1
    log_success(f"{'已寫入' if changed else '已經是最新的'} {rc} 裡的 acg msg 區塊;開新的終端機後生效")
    log_info("平常照打 claude、codex。要讓 Codex 等其他 session 傳話給 Claude,就用 claude-msg 開;")
    log_info("它每次啟動會問一次 development channel,按 Enter 即可(Claude Code 的規定,關不掉)")
    if "codex" in skipped:
        log_info("你已經有自己的 codex 函式,沒有加 acg 的;要讓 Codex 收得到訊息,")
        log_info('它開 TUI 時要帶 --remote unix:// --cd "$PWD"(resume/fork 不帶 --cd)')
    if os.name == "nt" and _profile_blocked():
        # Windows 用戶端預設執行原則是 Restricted,$PROFILE 根本不會載入
        log_info("PowerShell 目前不載入 $PROFILE;要讓函式生效,執行一次:")
        log_info("  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned")
    return 0


def _profile_blocked() -> bool:
    import subprocess

    from ..subproc import NATIVE

    try:
        policy = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-ExecutionPolicy"],
            capture_output=True, text=True, **NATIVE, check=False, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return policy in {"Restricted", "AllSigned"}


def run_msg(args: list) -> int:
    action = args[0] if args else "list"
    if action in {"--help", "-h"}:
        log_info(_USAGE.format(entry=ENTRYPOINT))
        return 0
    try:
        if action == "list" and len(args) <= 1:
            return _list()
        if action == "setup" and args[1:] in ([], ["--print"]):
            return _setup(print_only=args[1:] == ["--print"])
        if action == "send":
            return _send(args[1:])
    except messaging.MessagingError as exc:
        log_error(str(exc))
        return 1
    log_error(_USAGE.format(entry=ENTRYPOINT))
    return 1
