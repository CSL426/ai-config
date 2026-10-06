"""Passing messages between live agent sessions.

Claude sessions can already reach each other; Codex and Antigravity
sessions could not be reached at all. acg acts as the post office: it
lists who is on line across tools and delivers a message by name.

Codex: a TUI started with `--remote unix://` attaches to the account's
app-server daemon, which speaks JSON-RPC over a WebSocket on a unix
socket. The daemon lists loaded threads and queues a message into one
(`thread/queue/add`, the call `codex queue` makes). Everything here is
Codex's own internal protocol, so failures are reported plainly rather
than papered over.

Windows: the daemon's socket is a real AF_UNIX socket there too, but
CPython on Windows has no AF_UNIX, so acg talks through
`codex app-server proxy`, which relays its stdin and stdout to the
socket. acg's own Claude channel listens on a named pipe instead.

herdr: an agent started in a herdr pane, of any tool, is reached by
herdr typing the message into its terminal (`herdr agent prompt`). That
is how an Antigravity conversation receives at all, and a Codex there
needs no `--remote`.
"""

import base64
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .paths import HOME
from .processes import pid_alive
from .subproc import NATIVE

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
    # "herdr":由 herdr 打字進它的終端機;空字串是工具自己的管道
    via: str = ""
    # 也在 herdr 窗格裡跑時的窗格編號與 herdr 名稱:合併成一行後還要找得到它
    pane: str = ""
    pane_name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.id


class _ProxyStream:
    """The daemon's socket through `codex app-server proxy`, for a Python without AF_UNIX."""

    def __init__(self, path: Path, timeout: float) -> None:
        self._timeout = timeout
        self._proc = subprocess.Popen(
            _proxy_argv(path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._chunks: queue.Queue = queue.Queue()
        self._ended = False
        # pipe 在 Windows 上不能 select;讀取交給一條執行緒,逾時才有辦法做
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        while True:
            try:
                chunk = self._proc.stdout.read1(65536)
            except (OSError, ValueError):
                chunk = b""
            self._chunks.put(chunk)
            if not chunk:
                return

    def sendall(self, data: bytes) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def recv(self, _size: int) -> bytes:
        if self._ended:
            return b""
        try:
            chunk = self._chunks.get(timeout=self._timeout)
        except queue.Empty:
            raise TimeoutError("codex app-server proxy 沒有回應") from None
        self._ended = not chunk
        return chunk

    def close(self) -> None:
        # 先關 pipe:proxy 讀到 EOF 就自己結束。npm 版是 cmd → node → codex.exe,
        # kill 只殺得到最外層的 cmd,裡面兩層要靠 pipe 斷掉才會退
        for stream in (self._proc.stdin, self._proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()


def _proxy_argv(path: Path) -> list:
    return [_codex_binary(), "app-server", "proxy", "--sock", str(path)]


_VIA_PROXY = not hasattr(socket, "AF_UNIX")


def _connect(path: Path, timeout: float):
    if _VIA_PROXY:
        return _ProxyStream(path, timeout)
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.settimeout(timeout)
        sock.connect(str(path))
    except OSError:
        sock.close()
        raise
    return sock


class _WebSocket:
    """Just enough of RFC 6455 for one JSON-RPC client over the daemon's socket."""

    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        self._sock = _connect(path, timeout)
        try:
            self._handshake()
        except BaseException:
            # proxy 是子行程,握手失敗不收掉就會留在背景
            self._sock.close()
            raise

    def _handshake(self) -> None:
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
        try:
            # thread/queue/add 是 experimental 方法,要先宣告才能用
            self.call("initialize", {
                "clientInfo": {"name": "acg", "version": "1"},
                "capabilities": {"experimentalApi": True},
            })
            self._ws.send({"jsonrpc": "2.0", "method": "initialized"})
        except BaseException:
            self._ws.close()
            raise

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
        # Windows 的 AF_UNIX socket 是 reparse point;不跟著它走,只看它在不在
        if home.is_dir() and os.path.lexists(socket_path(home)):
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
                    # 子 agent 的 source 是物件;ephemeral 是 codex 產生標題這類一次性的 thread。
                    # 兩種都是工具,不是能講話的對象
                    if isinstance(thread.get("source"), dict) or thread.get("ephemeral"):
                        continue
                    peers.append(Peer(
                        tool="codex", id=thread_id, name=thread.get("name") or "",
                        account=_account(home), cwd=thread.get("cwd") or "",
                        status=(thread.get("status") or {}).get("type", ""), home=home,
                    ))
        except (OSError, MessagingError):
            continue  # socket 留著但 daemon 已經不在;列表不因一個帳號壞掉就失敗
    return peers


def channel_dir() -> Path:
    """Where acg's Claude channel servers leave their delivery sockets."""
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "acg" / "msg" / "claude"


# Windows 上 channel 聽的是 named pipe;這個檔只記 pipe 名稱,有它才算收得到
_CHANNEL_SUFFIX = ".pipe" if os.name == "nt" else ".sock"


def channel_socket(claude_pid: int) -> Path:
    """Where a Claude session's channel can be reached: the socket, or on Windows the pipe's name."""
    return channel_dir() / f"{claude_pid}{_CHANNEL_SUFFIX}"


def claude_peers() -> list:
    """Running Claude Code sessions, from the records Claude keeps per process.

    sessions/<pid>.json is Claude Code's internal state, not an interface;
    anything unreadable is skipped rather than guessed at.
    """
    from .paths import CLAUDE_HOME

    # 被 signal 直接殺掉的 channel 來不及收尾;行程已經不在的 socket 順手清掉
    for stale in channel_dir().glob(f"*{_CHANNEL_SUFFIX}") if channel_dir().is_dir() else ():
        try:
            if not pid_alive(int(stale.stem)):
                stale.unlink()
        except (ValueError, OSError):
            continue
    peers = []
    for path in sorted((CLAUDE_HOME / "sessions").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            pid = int(record["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if record.get("kind") not in (None, "interactive") or not pid_alive(pid):
            continue
        reachable = channel_socket(pid).exists()
        peers.append(Peer(
            tool="claude", id=str(record.get("sessionId") or pid),
            name=str(record.get("name") or ""), account="", cwd=str(record.get("cwd") or ""),
            status=str(record.get("status") or ""), home=None, pid=pid,
            # 送信不需要 channel,只有收信要;說清楚免得對方以為自己的送信壞了
            unreachable="" if reachable else "這個 session 不是用 claude-msg 開的,只能發訊息、收不到",
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
    root = HOME / ".gemini" / "antigravity-cli"
    peers = []
    for lock in sorted((root / "presence").glob("*.lock")):
        if not _lock_held(lock):
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
            pid=_lock_holder(lock),
            unreachable=(
                "Antigravity 開著的對話收不到外部訊息;它能用 acg msg send 主動傳話,"
                "在 herdr 裡開的 Antigravity 則收得到"
            ),
        ))
    return peers


def _lock_holder(path: Path) -> int:
    """The pid holding a flock on this file, from /proc/locks; 0 where that cannot be told."""
    try:
        found = path.stat()
        listed = Path("/proc/locks").read_text(encoding="utf-8")
    except OSError:
        return 0
    # 1: FLOCK  ADVISORY  WRITE 2840782 08:02:102367394 0 EOF(裝置號是十六進位)
    where = f"{os.major(found.st_dev):02x}:{os.minor(found.st_dev):02x}:{found.st_ino}"
    for line in listed.splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[1] == "FLOCK" and fields[5] == where and fields[4].isdigit():
            return int(fields[4])
    return 0


def _herdr_socket() -> str:
    """The socket of the herdr session herdr's own CLI talks to.

    HERDR_SOCKET_PATH wins over HERDR_SESSION, which wins over the default
    session (checked against herdr 0.9.3). Empty when it cannot be told.
    """
    explicit = os.environ.get("HERDR_SOCKET_PATH", "").strip()
    if explicit:
        return explicit
    wanted = os.environ.get("HERDR_SESSION", "").strip()
    listed = _herdr("session", "list", "--json", timeout=5.0)
    sessions = (listed or {}).get("sessions")
    for session in sessions if isinstance(sessions, list) else []:
        if not isinstance(session, dict):
            continue
        if (session.get("name") == wanted) if wanted else session.get("default"):
            return str(session.get("socket_path") or "")
    return ""


def _herdr_pane(pid: int, socket_path: str) -> str:
    """The pane of that herdr session a process runs in; empty where that cannot be told.

    Pane ids repeat across herdr sessions: every session has a w1:p1.
    """
    if not pid or not socket_path:
        return ""
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return ""
    values = dict(
        entry.decode("utf-8", "replace").split("=", 1)
        for entry in environ.split(b"\0") if b"=" in entry
    )
    if os.path.normpath(values.get("HERDR_SOCKET_PATH", "")) != os.path.normpath(socket_path):
        return ""
    return values.get("HERDR_PANE_ID", "")


def _started(pid: int) -> float:
    """When a process started, in epoch seconds; 0 where that cannot be told."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        booted = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        return booted + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration, AttributeError):
        return 0.0


def _codex_clients(socket_path: str) -> list:
    """(pane, start time) of each codex process running in that herdr session's panes."""
    found = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text(encoding="utf-8").strip() != "codex":
                continue
        except OSError:
            continue
        pane = _herdr_pane(int(entry.name), socket_path)
        started = _started(int(entry.name)) if pane else 0.0
        if pane and started:
            found.append((pane, started))
    return found


def _thread_born(thread_id: str) -> float:
    """When a Codex thread was created: its id is a UUIDv7, led by the time in milliseconds.

    The daemon's createdAt is reset when it loads a thread again, hours later.
    """
    digits = thread_id.replace("-", "")
    if len(digits) != 32 or digits[12:13] != "7":
        return 0.0
    try:
        return int(digits[:12], 16) / 1000
    except ValueError:
        return 0.0


def _codex_pane(peer: Peer, herdr: dict, clients: list) -> str:
    """The herdr pane whose codex started this daemon thread, when exactly one did.

    A daemon thread carries no client pid. A codex opened in a pane starts
    its thread within a second of starting; one resumed or begun with /new
    later does not match, and stays listed through both.
    """
    born = _thread_born(peer.id)
    if not born:
        return ""
    panes = {
        pane for pane, started in clients
        if 0 <= born - started <= 10 and pane in herdr
        and herdr[pane].tool == "codex" and herdr[pane].cwd == peer.cwd
    }
    return panes.pop() if len(panes) == 1 else ""


def _lock_held(path: Path) -> "bool | None":
    """Whether another process holds the lock on this file; None when it cannot be opened."""
    try:
        with open(path, "rb") as handle:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    return True
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                return False
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False
    except OSError:
        return None


def _herdr(*args: str, timeout: float = 10.0) -> "dict | None":
    """One herdr CLI call; None when herdr is missing or its server is not running."""
    binary = shutil.which("herdr")
    if binary is None:
        return None
    try:
        done = subprocess.run(
            # herdr 的輸出一律是 UTF-8;照系統語系解碼的話 Windows 上中文會亂掉
            [binary, *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for text in (done.stdout, done.stderr):
        try:
            value = json.loads(text)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def herdr_peers() -> list:
    """Agents running in the default herdr session's panes.

    herdr types into the pane's terminal, so every one of them can
    receive, whichever tool it is. Without herdr, or with no server
    running, there are simply none.
    """
    listed = _herdr("agent", "list", timeout=5.0)
    agents = (listed or {}).get("result", {}).get("agents", [])
    peers = []
    for agent in agents if isinstance(agents, list) else []:
        if not isinstance(agent, dict) or not agent.get("pane_id"):
            continue
        status = str(agent.get("agent_status", ""))
        peers.append(Peer(
            tool=str(agent.get("agent", "")), id=str(agent["pane_id"]),
            name=str(agent.get("name") or ""), account="herdr",
            cwd=str(agent.get("cwd", "")), status=status, via="herdr",
            unreachable=(
                "它停在確認或問題畫面,先在 herdr 裡處理" if status == "blocked" else ""
            ),
        ))
    return peers


def list_peers() -> list:
    """Every live agent once, even one that both its tool and herdr can see.

    An agent in a herdr pane also shows up through its own tool: Claude by
    its session record, Antigravity by its presence lock, Codex by the
    daemon thread its client started. The entry that can receive stays;
    when both can, the tool's own channel does.
    """
    local = [*claude_peers(), *codex_peers(), *agy_peers()]
    herdr = {peer.id: peer for peer in herdr_peers()}
    socket_path = _herdr_socket() if herdr else ""
    clients = (
        _codex_clients(socket_path) if any(p.tool == "codex" for p in herdr.values()) else []
    )
    kept = []
    for peer in local:
        pane = _herdr_pane(peer.pid, socket_path) or (
            _codex_pane(peer, herdr, clients) if peer.tool == "codex" else ""
        )
        if pane not in herdr:
            kept.append(peer)
        elif peer.unreachable:
            continue
        else:
            # 在 herdr 取的名字和窗格編號仍然要能用來送
            twin = herdr.pop(pane)
            peer.pane, peer.pane_name = pane, twin.name
            peer.name = peer.name or twin.name
            kept.append(peer)
    return [*kept, *herdr.values()]


def outside_herdr_agy(peers: list) -> bool:
    """Whether an Antigravity runs where no message reaches it.

    Without /proc an agy in a herdr pane is listed twice, so only more
    local entries than herdr ones show one is really outside.
    """
    local = sum(1 for peer in peers if peer.tool == "agy" and peer.via != "herdr")
    return local > sum(1 for peer in peers if peer.tool == "agy" and peer.via == "herdr")


def shared_ids(peers: list) -> set:
    """Ids held by more than one process, e.g. one Claude session resumed twice."""
    seen: set = set()
    shared: set = set()
    for peer in peers:
        (shared if peer.id in seen else seen).add(peer.id)
    return shared


def resolve(target: str, peers: "list | None" = None) -> Peer:
    """A peer by exact name, full id, herdr pane or name, an id prefix of eight or more, or pid."""
    peers = list_peers() if peers is None else peers
    exact = [p for p in peers if target in {p.name, p.id, p.pane, p.pane_name} - {""}]
    if not exact and len(target) >= 8:
        exact = [p for p in peers if p.id.startswith(target)]
    if not exact and target.isdigit():
        exact = [p for p in peers if p.pid and str(p.pid) == target]
    if len(exact) == 1:
        return exact[0]
    if not exact:
        raise MessagingError(f"找不到在線上的「{target}」;用 acg msg list 看有誰")
    names = "、".join(
        f"{p.label}({p.tool} {p.account}".rstrip() + (f" pid {p.pid})" if p.pid else ")")
        for p in exact
    )
    # 同一個 session 被兩個行程掛著時 id 也一樣,只剩 pid 分得開
    hint = "改用 pid" if len({p.id for p in exact}) == 1 else "改用 id"
    raise MessagingError(f"「{target}」對到不只一個:{names};{hint}")


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
    data = json.dumps({"from": sender, "text": text}, ensure_ascii=False).encode()
    try:
        if os.name == "nt":
            answer = _send_pipe(path.read_text(encoding="utf-8").strip(), data)
        else:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(10)
                conn.connect(str(path))
                conn.sendall(data + b"\n")
                answer = conn.recv(64).strip()
    except (OSError, EOFError) as exc:
        raise MessagingError(f"送不進 {peer.label} 的 channel:{exc}") from exc
    if answer != b"ok":
        raise MessagingError(f"{peer.label} 的 channel 拒收了這則訊息")


def _send_pipe(address: str, data: bytes) -> bytes:
    from multiprocessing.connection import Client

    if not address.startswith("\\\\.\\pipe\\"):
        raise OSError(f"不是 named pipe:{address}")
    with Client(address, family="AF_PIPE") as conn:
        conn.send_bytes(data)
        if not conn.poll(10):
            raise TimeoutError("channel 沒有回應")
        return conn.recv_bytes(64).strip()


def _codex_binary() -> str:
    found = shutil.which("codex")
    if not found:
        raise MessagingError("找不到 codex 指令")
    return found


def send_codex(peer: Peer, body: str) -> None:
    """Queue a message into the thread, the same call `codex queue` makes.

    Not by running `codex queue`: on Windows that is codex.cmd, and cmd.exe
    cuts an argument at its first newline, so only the header arrived.
    """
    assert peer.home is not None
    try:
        with CodexDaemon(peer.home) as daemon:
            daemon.call("thread/queue/add", {
                "threadId": peer.id, "clientUserMessageId": str(uuid.uuid4()),
                "input": [{"type": "text", "text": body, "text_elements": []}],
            })
    except OSError as exc:
        raise MessagingError(f"連不上 {peer.label} 的 Codex daemon:{exc}") from exc


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


_ROLLOUT_EMPTY = "is empty"


def wait_codex_reply(peer: Peer, tag: str, timeout: float, poll: float = 2.0) -> str:
    """The answer to the turn our message started, once that turn ends."""
    assert peer.home is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with CodexDaemon(peer.home) as daemon:
                turns = daemon.call("thread/turns/list", {
                    "threadId": peer.id, "limit": 10, "itemsView": "full",
                }).get("data", [])
        except MessagingError as exc:
            # 新 thread 的第一輪剛開始時 rollout 檔還是空的,daemon 讀它會報錯;
            # 寫進去之後就讀得到,這不是送信失敗
            if _ROLLOUT_EMPTY not in str(exc):
                raise
            turns = []
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


def _after_tag(screen: str, tag: str, body: str = "") -> str:
    """The agent's reply: what the pane shows after the message carrying the tag."""
    lines = screen.splitlines()
    marked = [index for index, line in enumerate(lines) if f"#{tag}" in line]
    if not marked:
        return ""
    tail = lines[marked[-1] + 1:]
    # 畫面先重印一次訊息本身(終端機會自動換行),之後才是對方的回答。
    # 要從訊息開頭一段一段接著對,不能只看「有沒有出現在訊息裡」:
    # 回答常常就是訊息裡要求的那句話
    rest = " ".join(body.split("\n", 1)[1].split()) if "\n" in body else ""
    while tail:
        piece = " ".join(tail[0].split())
        if not piece:
            tail = tail[1:]
            continue
        if not rest.startswith(piece):
            break
        rest = rest[len(piece):].lstrip()
        tail = tail[1:]
    # 回答之後是輸入框:分隔線、提示符號,再下面是模型名稱與額度條,都不是回答
    for index, line in enumerate(tail):
        stripped = line.strip()
        if len(stripped) >= 10 and set(stripped) <= set("─━═-"):
            tail = tail[:index]
            break
        if stripped in (">", "›", "❯") or stripped.startswith(("› ", "❯ ")):
            tail = tail[:index]
            break
    # 回答前它可能先想、先查:收起來的思考是標題加一行摘要,工具呼叫一行一個
    start = 0
    for index, line in enumerate(tail):
        stripped = line.strip()
        if stripped.startswith("▸ Thought for "):
            start = index + 2
        elif _ACTIVITY.match(stripped):
            start = index + 1
    return "\n".join(tail[start:]).strip()


_ACTIVITY = re.compile(r"^(● \w+\(.*\)( \(ctrl\+o to expand\))?|⎿.*)$")


def send_herdr(peer: Peer, body: str, tag: str, wait: float) -> str:
    """Have herdr type the message into the agent's pane; with wait, read its reply."""
    args = ["agent", "prompt", peer.id, body]
    if wait > 0:
        args += ["--wait", "--timeout", str(int(wait * 1000))]
    result = _herdr(*args, timeout=(wait + 30) if wait > 0 else 30)
    if result is None:
        raise MessagingError("herdr 沒有回應;確認 herdr 在跑(herdr status)")
    error = result.get("error")
    if isinstance(error, dict):
        raise MessagingError(f"herdr:{error.get('message') or error.get('code')}")
    if wait <= 0:
        return ""
    read = _herdr_text("agent", "read", peer.id, "--source", "recent-unwrapped", "--lines", "200")
    return _after_tag(read, tag, body)


def _herdr_text(*args: str) -> str:
    binary = shutil.which("herdr")
    if binary is None:
        return ""
    try:
        done = subprocess.run(
            [binary, *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


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
    if peer.via == "herdr":
        body, tag = compose(text, sender, reply_as=peer.label if _can_receive(sender) else "")
        return peer, send_herdr(peer, body, tag, wait)
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


# 掛 channel 的 Claude 每次啟動都要人按一次 development channel 警告,所以只在要收訊息時才用;
# 平常的 claude 不動
CLAUDE_MSG_FUNCTION = """claude-msg() {{
  command claude --mcp-config {config} --dangerously-load-development-channels server:acg "$@"
}}"""


# 開 TUI 才掛上 daemon,exec、queue 這類子指令照原樣跑。daemon 的工作目錄是它
# 啟動時的目錄,新對話沒帶 --cd 會跑到那裡去;resume/fork 沿用舊對話的目錄,不帶
CODEX_FUNCTION = """codex() {{
  local attach=""
  case "${{1:-}}" in
    -h|--help|-V|--version) ;;
    ""|-*|resume|fork) attach=1 ;;
    {subcommands}) ;;
    *) attach=1 ;;
  esac
  case " $* " in *" --remote "*|*" --remote="*) attach="" ;; esac
  if [ -n "$attach" ]; then
    local home="${{CODEX_HOME:-$HOME/.codex}}"
    if [ ! -S "$home/{socket}" ]; then
      CODEX_HOME="$home" command codex app-server daemon start >/dev/null 2>&1
    fi
    if [ "${{1:-}}" = resume ] || [ "${{1:-}}" = fork ]; then
      local sub="$1"; shift
      set -- "$sub" --remote unix:// "$@"
    else
      case " $* " in
        *" -C "*|*" --cd "*|*" --cd="*) set -- --remote unix:// "$@" ;;
        *) set -- --remote unix:// --cd "$PWD" "$@" ;;
      esac
    fi
  fi
  command codex "$@"
}}"""

# codex 0.159 不開 TUI 的子指令;codex 新增子指令時要跟著加,否則會被當成提示文字、多帶上 --remote
CODEX_SUBCOMMANDS = (
    "agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
    "remote-control", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
    "queue", "archive", "delete", "migrate-rollouts", "unarchive", "cloud", "exec-server",
    "features", "help",
)


# PowerShell 版:同樣的判斷。比對一律分大小寫,codex 的 -c 是設定、-C 才是目錄。
# 只用 ASCII:Windows PowerShell 5.1 把沒有 BOM 的 profile 當系統碼頁讀
POWERSHELL_CLAUDE_MSG = """function claude-msg {{
  $exe = (Get-Command claude -CommandType Application -ErrorAction Stop)[0].Source
  & $exe --mcp-config {config} --dangerously-load-development-channels server:acg @args
}}"""
POWERSHELL_CODEX = """function codex {{
  $exe = (Get-Command codex -CommandType Application -ErrorAction Stop)[0].Source
  $first = if ($args.Count) {{ [string]$args[0] }} else {{ '' }}
  $attach = -not ($first -cin @('-h', '--help', '-V', '--version', {codex_plain}))
  if ($args -ccontains '--remote' -or @($args | Where-Object {{ "$_" -clike '--remote=*' }}).Count) {{
    $attach = $false
  }}
  if (-not $attach) {{ & $exe @args; return }}
  $codexHome = if ($env:CODEX_HOME) {{ $env:CODEX_HOME }} else {{ Join-Path $HOME '.codex' }}
  if (-not (Test-Path -LiteralPath (Join-Path $codexHome '{socket}'))) {{
    $saved = $env:CODEX_HOME
    $env:CODEX_HOME = $codexHome
    & $exe app-server daemon start *> $null
    $env:CODEX_HOME = $saved
  }}
  if ($first -cin @('resume', 'fork')) {{
    $rest = @($args | Select-Object -Skip 1)
    & $exe $first --remote unix:// @rest; return
  }}
  if ($args -ccontains '-C' -or $args -ccontains '--cd' -or @($args | Where-Object {{ "$_" -clike '--cd=*' }}).Count) {{
    & $exe --remote unix:// @args; return
  }}
  & $exe --remote unix:// --cd $PWD.ProviderPath @args
}}"""

BLOCK_BEGIN = "# >>> acg msg >>>"
BLOCK_END = "# <<< acg msg <<<"
_BLOCK_NOTE = "# Written by acg msg setup; run it again to update this block."


def _ps_list(words) -> str:
    return ", ".join(f"'{word}'" for word in words)


def claude_msg_function() -> str:
    return CLAUDE_MSG_FUNCTION.format(config=shlex.quote(str(channel_config_path())))


def codex_function() -> str:
    return CODEX_FUNCTION.format(subcommands="|".join(CODEX_SUBCOMMANDS), socket=SOCKET.as_posix())


def _powershell_quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def powershell_claude_msg() -> str:
    return POWERSHELL_CLAUDE_MSG.format(config=_powershell_quoted(channel_config_path()))


def powershell_codex() -> str:
    return POWERSHELL_CODEX.format(
        codex_plain=_ps_list(CODEX_SUBCOMMANDS), socket=str(SOCKET).replace("/", "\\"),
    )


def shell_block(windows: "bool | None" = None, skip: "frozenset | set" = frozenset()) -> str:
    """The block `acg msg setup` keeps in ~/.bashrc, or on Windows in $PROFILE.

    `skip` names functions the file already defines itself (say a codex()
    that switches accounts): defining ours after it would replace it.
    """
    windows = os.name == "nt" if windows is None else windows
    parts = [BLOCK_BEGIN, _BLOCK_NOTE]
    parts.append(powershell_claude_msg() if windows else claude_msg_function())
    if "codex" not in skip:
        parts.append(powershell_codex() if windows else codex_function())
    parts.append(BLOCK_END)
    return "\n".join(parts)


def shell_functions() -> str:
    return shell_block()


def rc_path() -> Path:
    """The file the block goes in: $PROFILE on Windows, else the login shell's rc."""
    if os.name == "nt":
        try:
            found = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", "$PROFILE"],
                capture_output=True, text=True, **NATIVE, check=False, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            found = ""
        if found:
            return Path(found)
        return HOME / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
    shell = Path(os.environ.get("SHELL", "")).name
    return HOME / (".zshrc" if shell == "zsh" else ".bashrc")


def _outside_block(text: str) -> str:
    kept, inside = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == BLOCK_BEGIN:
            inside = True
        elif inside and line.strip() == BLOCK_END:
            inside = False
        elif not inside:
            kept.append(line)
    return "".join(kept)


def _defines(text: str, name: str, windows: bool) -> bool:
    pattern = (
        rf"^\s*function\s+{re.escape(name)}\b" if windows
        else rf"^\s*(?:function\s+{re.escape(name)}\b|{re.escape(name)}\s*\(\s*\))"
    )
    return re.search(pattern, text, re.MULTILINE | (re.IGNORECASE if windows else 0)) is not None


def install_shell_block(path: "Path | None" = None, windows: "bool | None" = None) -> "tuple[Path, set, bool]":
    """Write or refresh the block in the shell's startup file: (file, skipped, changed).

    Everything outside the markers is left byte for byte, including a BOM.
    A file that is not UTF-8 is refused rather than rewritten in the wrong
    encoding; the caller then prints the block for the user to add.
    """
    windows = os.name == "nt" if windows is None else windows
    path = path or rc_path()
    raw = path.read_bytes() if path.exists() else b""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = raw[len(bom):].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MessagingError(f"{path} 不是 UTF-8,沒有改它") from exc
    outside = _outside_block(text)
    skipped = {name for name in ("codex",) if _defines(outside, name, windows)}
    head = outside.rstrip("\n")
    updated = (head + "\n\n" if head else "") + shell_block(windows, skipped) + "\n"
    if updated == text:
        return path, skipped, False
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw:
        path.with_name(path.name + ".acg-bak").write_bytes(raw)
    staged = path.with_name(f".{path.name}.acg-new")
    staged.write_bytes(bom + updated.encode("utf-8"))
    os.replace(staged, path)
    return path, skipped, True
