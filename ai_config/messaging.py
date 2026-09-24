"""Passing messages between live agent sessions.

Claude sessions can already reach each other; Codex and Antigravity
sessions could not be reached at all. acg acts as the post office: it
lists who is on line across tools and delivers a message by name.

Codex: a TUI started with `--remote unix://` attaches to the account's
app-server daemon, which speaks JSON-RPC over a WebSocket on a unix
socket. The daemon lists loaded threads and `codex queue` delivers into
one. Everything here is Codex's own internal protocol, so failures are
reported plainly rather than papered over.
"""

import base64
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .paths import HOME
from .subproc import UTF8

SOCKET = Path("app-server-control") / "app-server-control.sock"


class MessagingError(Exception):
    """A message could not be listed, delivered or answered."""


@dataclass
class Peer:
    tool: str
    id: str
    name: str
    account: str
    cwd: str
    status: str
    home: "Path | None" = None
    pid: int = 0
    # 收不到時說明原因;空字串代表送得進去
    unreachable: str = ""

    @property
    def label(self) -> str:
        return self.name or self.id


class _WebSocket:
    """Just enough of RFC 6455 for one JSON-RPC client over a unix socket."""

    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        self._sock = socket.socket(socket.AF_UNIX)
        self._sock.settimeout(timeout)
        self._sock.connect(str(path))
        key = base64.b64encode(os.urandom(16)).decode()
        self._sock.sendall((
            "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise MessagingError("Codex daemon 沒有完成連線")
            head += chunk
        status, _, rest = head.partition(b"\r\n\r\n")
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise MessagingError("Codex daemon 拒絕了連線")
        self._buffer = rest

    def close(self) -> None:
        self._sock.close()

    def send(self, message: dict) -> None:
        data = json.dumps(message).encode()
        mask = os.urandom(4)
        size = len(data)
        if size < 126:
            head = bytes([0x81, 0x80 | size])
        elif size < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", size)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", size)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self._sock.sendall(head + mask + masked)

    def _read(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise MessagingError("Codex daemon 中斷了連線")
            self._buffer += chunk
        out, self._buffer = self._buffer[:size], self._buffer[size:]
        return out

    def receive(self) -> dict:
        while True:
            first, second = self._read(2)
            size = second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(size)
            opcode = first & 0x0F
            if opcode == 8:
                raise MessagingError("Codex daemon 關閉了連線")
            if opcode in (1, 2):
                return json.loads(payload)


class CodexDaemon:
    """One account's app-server daemon, reached over its control socket."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._ws = _WebSocket(socket_path(home))
        self._next = 0
        self.call("initialize", {"clientInfo": {"name": "acg", "version": "1"}})
        self._ws.send({"jsonrpc": "2.0", "method": "initialized"})

    def __enter__(self) -> "Self":
        return self

    def __exit__(self, *_exc) -> None:
        self._ws.close()

    def call(self, method: str, params: dict) -> dict:
        self._next += 1
        ident = self._next
        self._ws.send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        while True:
            message = self._ws.receive()
            if message.get("id") != ident:
                continue  # 通知與別人的回應,這裡用不到
            if "error" in message:
                raise MessagingError(f"{method}:{message['error'].get('message', message['error'])}")
            return message.get("result") or {}


def socket_path(home: Path) -> Path:
    return home / SOCKET


def codex_homes_with_daemon() -> list:
    """Every ~/.codex* home whose daemon socket is there.

    One daemon per CODEX_HOME, not per account: two homes sharing a
    login still run separate daemons, and a TUI attaches to the one of
    the home it was started in.
    """
    homes = []
    for home in sorted(HOME.glob(".codex*")):
        if home.is_dir() and socket_path(home).exists():
            homes.append(home)
    return homes


def _account(home: Path) -> str:
    return home.name.removeprefix(".codex").lstrip("-") or "default"


def codex_peers() -> list:
    """Threads the daemons have loaded, minus the sub-agents they spawned."""
    peers = []
    for home in codex_homes_with_daemon():
        try:
            with CodexDaemon(home) as daemon:
                ids = daemon.call("thread/loaded/list", {}).get("data", [])
                for thread_id in ids:
                    thread = daemon.call("thread/read", {"threadId": thread_id}).get("thread", {})
                    # 子 agent 的 source 是物件;它們是對話裡的工具,不是能講話的對象
                    if isinstance(thread.get("source"), dict):
                        continue
                    peers.append(Peer(
                        tool="codex", id=thread_id, name=thread.get("name") or "",
                        account=_account(home), cwd=thread.get("cwd") or "",
                        status=(thread.get("status") or {}).get("type", ""), home=home,
                    ))
        except (OSError, MessagingError):
            continue  # socket 留著但 daemon 已經不在;列表不因一個帳號壞掉就失敗
    return peers


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def channel_dir() -> Path:
    """Where acg's Claude channel servers leave their delivery sockets."""
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "acg" / "msg" / "claude"


def channel_socket(claude_pid: int) -> Path:
    return channel_dir() / f"{claude_pid}.sock"


def claude_peers() -> list:
    """Running Claude Code sessions, from the records Claude keeps per process.

    sessions/<pid>.json is Claude Code's internal state, not an interface;
    anything unreadable is skipped rather than guessed at.
    """
    from .paths import CLAUDE_HOME

    peers = []
    for path in sorted((CLAUDE_HOME / "sessions").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            pid = int(record["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if record.get("kind") not in (None, "interactive") or not _pid_alive(pid):
            continue
        reachable = channel_socket(pid).exists()
        peers.append(Peer(
            tool="claude", id=str(record.get("sessionId") or pid),
            name=str(record.get("name") or ""), account="", cwd=str(record.get("cwd") or ""),
            status=str(record.get("status") or ""), home=None, pid=pid,
            # 送信不需要 channel,只有收信要;說清楚免得對方以為自己的送信壞了
            unreachable="" if reachable else "這個 session 不是用 acg channel 開的,只能發訊息、收不到",
        ))
    return peers


_TITLE = re.compile(r'^title:\s*"(.*)"\s*$', re.MULTILINE)


def agy_peers() -> list:
    """Antigravity conversations open in a TUI right now.

    An open conversation holds a flock on presence/<id>.lock; the files
    themselves stay behind long after, so only the held lock counts.
    Nothing can be delivered into an open TUI: it keeps its own state and
    a message sent past it forks the conversation.
    """
    try:
        import fcntl
    except ImportError:
        return []  # Windows 上沒有 flock;先不列
    root = HOME / ".gemini" / "antigravity-cli"
    peers = []
    for lock in sorted((root / "presence").glob("*.lock")):
        try:
            with open(lock, "rb") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    held = True
                else:
                    held = False
                    fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            continue
        if not held:
            continue
        conversation = lock.stem
        title = ""
        try:
            found = _TITLE.search((root / "annotations" / f"{conversation}.pbtxt").read_text(encoding="utf-8"))
            title = found.group(1) if found else ""
        except OSError:
            pass
        peers.append(Peer(
            tool="agy", id=conversation, name=title, account="", cwd="", status="open",
            unreachable="Antigravity 開著的對話收不到外部訊息;它能用 acg msg send 主動傳話",
        ))
    return peers


def list_peers() -> list:
    return [*claude_peers(), *codex_peers(), *agy_peers()]


def resolve(target: str, peers: "list | None" = None) -> Peer:
    """A peer by exact name, full id, or an id prefix of eight or more."""
    peers = list_peers() if peers is None else peers
    exact = [p for p in peers if target in (p.name, p.id)]
    if not exact and len(target) >= 8:
        exact = [p for p in peers if p.id.startswith(target)]
    if len(exact) == 1:
        return exact[0]
    if not exact:
        raise MessagingError(f"找不到在線上的「{target}」;用 acg msg list 看有誰")
    names = "、".join(f"{p.label}({p.tool} {p.account})" for p in exact)
    raise MessagingError(f"「{target}」對到不只一個:{names};改用 id")


def sender_name() -> str:
    """Who this message is from, as the recipient should address the reply."""
    explicit = os.environ.get("ACG_MSG_FROM", "").strip()
    if explicit:
        return explicit
    from .handoff import session_name

    return session_name() or "(未具名的 session)"


def compose(text: str, sender: str, reply_as: str = "") -> "tuple[str, str]":
    """The delivered text, and the tag that finds its turn again.

    reply_as is the recipient's own name: its reply must say who it is
    from, and a Codex or Antigravity shell has no session name to find.
    """
    tag = secrets.token_hex(3)
    body = f"[acg 訊息 #{tag},來自 {sender}]\n{text}"
    if reply_as:
        body += (
            "\n\n(要回覆就執行:acg msg send "
            f"{shlex.quote(sender)} \"<內容>\" --from {shlex.quote(reply_as)})"
        )
    return body, tag


def _can_receive(name: str) -> bool:
    """Whether a reply addressed to this sender would reach anyone."""
    try:
        return any(not peer.unreachable for peer in list_peers() if name in (peer.name, peer.id))
    except OSError:
        return False


def send_claude(peer: Peer, sender: str, text: str) -> None:
    path = channel_socket(peer.pid)
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(10)
            conn.connect(str(path))
            conn.sendall(json.dumps({"from": sender, "text": text}, ensure_ascii=False).encode() + b"\n")
            answer = conn.recv(64).strip()
    except OSError as exc:
        raise MessagingError(f"送不進 {peer.label} 的 channel:{exc}") from exc
    if answer != b"ok":
        raise MessagingError(f"{peer.label} 的 channel 拒收了這則訊息")


def _codex_binary() -> str:
    found = shutil.which("codex")
    if not found:
        raise MessagingError("找不到 codex 指令")
    return found


def send_codex(peer: Peer, body: str) -> None:
    assert peer.home is not None
    env = {**os.environ, "CODEX_HOME": str(peer.home)}
    result = subprocess.run(
        [_codex_binary(), "queue", "--remote", f"unix://{socket_path(peer.home)}",
         "--thread", peer.id, "--message", body],
        capture_output=True, text=True, **UTF8, timeout=30, check=False, env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise MessagingError(f"codex queue 失敗:{detail[-1] if detail else result.returncode}")


def _turn_has_tag(turn: dict, tag: str) -> bool:
    for item in turn.get("items") or []:
        if item.get("type") != "userMessage":
            continue
        for part in item.get("content") or []:
            if f"#{tag}" in (part.get("text") or ""):
                return True
    return False


def _reply_text(turn: dict) -> str:
    texts = [
        item.get("text", "") for item in turn.get("items") or []
        if item.get("type") == "agentMessage" and item.get("text")
    ]
    finals = [
        item.get("text", "") for item in turn.get("items") or []
        if item.get("type") == "agentMessage" and item.get("phase") == "finalAnswer"
    ]
    return (finals or texts or [""])[-1]


def wait_codex_reply(peer: Peer, tag: str, timeout: float, poll: float = 2.0) -> str:
    """The answer to the turn our message started, once that turn ends."""
    assert peer.home is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with CodexDaemon(peer.home) as daemon:
            turns = daemon.call("thread/turns/list", {
                "threadId": peer.id, "limit": 10, "itemsView": "full",
            }).get("data", [])
        for turn in turns:
            if not _turn_has_tag(turn, tag):
                continue
            status = turn.get("status")
            if status == "completed":
                return _reply_text(turn)
            if status in ("failed", "interrupted"):
                reason = ((turn.get("error") or {}).get("message") or "").strip()
                what = "失敗" if status == "failed" else "被中斷"
                raise MessagingError(f"訊息已送達,但對方這一輪{what}了" + (f":{reason}" if reason else ""))
        time.sleep(poll)
    raise MessagingError(f"等了 {int(timeout)} 秒還沒有回覆;訊息已送達,稍後可在對方的畫面看")


def send(
    target: str, text: str, wait: float = 0.0, sender: str = "",
) -> "tuple[Peer, str]":
    """Deliver, and when asked, wait for the reply; returns (peer, reply)."""
    if not text.strip():
        raise MessagingError("訊息不能是空的")
    peer = resolve(target)
    if peer.unreachable:
        raise MessagingError(f"{peer.label} 收不到訊息:{peer.unreachable}")
    sender = sender.strip() or sender_name()
    if peer.tool == "claude":
        # channel 會把寄件人放進標籤屬性,內容不必再包一層
        send_claude(peer, sender, text)
        return peer, ""
    # 寄件人收不到信的話,附回覆指令只會讓對方撞牆
    body, tag = compose(text, sender, reply_as=peer.label if _can_receive(sender) else "")
    send_codex(peer, body)
    if wait <= 0:
        return peer, ""
    return peer, wait_codex_reply(peer, tag, wait)


def channel_config_path() -> Path:
    """The --mcp-config file that gives one Claude session acg's channel.

    Not a user-scope MCP registration: that would start the server in
    every session, and a session launched without --channels ignores the
    events, so it would list as reachable while dropping every message.
    """
    base = os.environ.get("XDG_DATA_HOME") or str(HOME / ".local" / "share")
    return Path(base) / "ai-config" / "claude-channel.json"


def write_channel_config(command: "list | None" = None) -> Path:
    from .paths import scheduled_command

    argv = [*(command or scheduled_command()), "__channel"]
    server = {"command": str(argv[0]), "args": [str(part) for part in argv[1:]]}
    path = channel_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"acg": server}}, indent=2) + "\n", encoding="utf-8")
    return path


# 只有互動式開 Claude 才帶 channel:-p 沒人能按掉每次都會跳的警告,子指令也用不到
SHELL_FUNCTION = """claude() {{
  case "${{1:-}}" in
    agents|attach|auth|auto-mode|doctor|gateway|import|install|logs|mcp|plugin|plugins|project|respawn|rm|setup-token|stop|kill|ultrareview|update|-p|--print|-v|--version|-h|--help)
      command claude "$@"; return ;;
  esac
  case " $* " in *" -p "*|*" --print "*) command claude "$@"; return ;; esac
  command claude --mcp-config {config} \\
    --dangerously-load-development-channels server:acg "$@"
}}"""


def shell_function() -> str:
    return SHELL_FUNCTION.format(config=shlex.quote(str(channel_config_path())))
