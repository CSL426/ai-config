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


def list_peers() -> list:
    return codex_peers()


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


def compose(text: str, sender: str, reply_hint: bool) -> "tuple[str, str]":
    """The delivered text, and the tag that finds its turn again."""
    tag = secrets.token_hex(3)
    body = f"[acg 訊息 #{tag},來自 {sender}]\n{text}"
    if reply_hint:
        body += f"\n\n(回覆請執行:acg msg send {shlex.quote(sender)} \"<內容>\")"
    return body, tag


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


def send(target: str, text: str, wait: float = 0.0) -> "tuple[Peer, str]":
    """Deliver, and when asked, wait for the reply; returns (peer, reply)."""
    if not text.strip():
        raise MessagingError("訊息不能是空的")
    peer = resolve(target)
    # 第一步只有 Codex 收件;Claude 還收不到,附回覆指令只會讓對方撞牆
    body, tag = compose(text, sender_name(), reply_hint=False)
    send_codex(peer, body)
    if wait <= 0:
        return peer, ""
    return peer, wait_codex_reply(peer, tag, wait)
