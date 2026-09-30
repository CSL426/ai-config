"""Passing messages between live agent sessions.

Claude sessions can already reach each other; Codex and Antigravity
sessions could not be reached at all. acg acts as the post office: it
lists who is on line across tools and delivers a message by name.

Codex: a TUI started with `--remote unix://` attaches to the account's
app-server daemon, which speaks JSON-RPC over a WebSocket on a unix
socket. The daemon lists loaded threads and queues a message into one
(`thread/queue/add`, the call `codex queue` makes). Everything here is Codex's own internal protocol, so failures are
reported plainly rather than papered over.

Windows: the daemon's socket is a real AF_UNIX socket there too, but
CPython on Windows has no AF_UNIX, so acg talks through
`codex app-server proxy`, which relays its stdin and stdout to the
socket. acg's own Claude channel listens on a named pipe instead.
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
            unreachable="Antigravity 開著的對話收不到外部訊息;它能用 acg msg send 主動傳話",
        ))
    return peers


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


def list_peers() -> list:
    return [*claude_peers(), *codex_peers(), *agy_peers()]


def shared_ids(peers: list) -> set:
    """Ids held by more than one process, e.g. one Claude session resumed twice."""
    seen: set = set()
    shared: set = set()
    for peer in peers:
        (shared if peer.id in seen else seen).add(peer.id)
    return shared


def resolve(target: str, peers: "list | None" = None) -> Peer:
    """A peer by exact name, full id, an id prefix of eight or more, or pid."""
    peers = list_peers() if peers is None else peers
    exact = [p for p in peers if target in (p.name, p.id)]
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
CLAUDE_PLAIN = (
    "agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install", "logs",
    "mcp", "plugin", "plugins", "project", "respawn", "rm", "setup-token", "stop", "kill",
    "ultrareview", "update", "-p", "--print", "-v", "--version", "-h", "--help",
)
SHELL_FUNCTION = """claude() {{
  case "${{1:-}}" in
    {plain})
      command claude "$@"; return ;;
  esac
  case " $* " in *" -p "*|*" --print "*) command claude "$@"; return ;; esac
  command claude --mcp-config {config} \\
    --dangerously-load-development-channels server:acg "$@"
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


# PowerShell 版:同樣的判斷。比對一律分大小寫,codex 的 -c 是設定、-C 才是目錄
POWERSHELL_FUNCTIONS = """# >>> acg msg >>>
# 照常打 claude、codex,別的 session 就能用 acg msg 傳話進來(acg msg setup 產生)
function claude {{
  $exe = (Get-Command claude -CommandType Application -ErrorAction Stop)[0].Source
  $first = if ($args.Count) {{ [string]$args[0] }} else {{ '' }}
  if ($first -cin @({claude_plain}) -or $args -ccontains '-p' -or $args -ccontains '--print') {{
    & $exe @args; return
  }}
  & $exe --mcp-config {config} --dangerously-load-development-channels server:acg @args
}}
function codex {{
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
}}
# <<< acg msg <<<"""


def _ps_list(words) -> str:
    return ", ".join(f"'{word}'" for word in words)


def claude_function() -> str:
    return SHELL_FUNCTION.format(
        plain="|".join(CLAUDE_PLAIN), config=shlex.quote(str(channel_config_path())),
    )


def codex_function() -> str:
    return CODEX_FUNCTION.format(subcommands="|".join(CODEX_SUBCOMMANDS), socket=SOCKET.as_posix())


def powershell_functions() -> str:
    config = str(channel_config_path()).replace("'", "''")
    return POWERSHELL_FUNCTIONS.format(
        claude_plain=_ps_list(CLAUDE_PLAIN), codex_plain=_ps_list(CODEX_SUBCOMMANDS),
        config=f"'{config}'", socket=str(SOCKET).replace("/", "\\"),
    )


def shell_functions() -> str:
    """The block `acg msg setup` asks the user to put in ~/.bashrc, or on Windows in $PROFILE."""
    if os.name == "nt":
        return powershell_functions()
    return (
        "# >>> acg msg >>>\n"
        "# 照常打 claude、codex,別的 session 就能用 acg msg 傳話進來(acg msg setup 產生)\n"
        f"{claude_function()}\n{codex_function()}\n"
        "# <<< acg msg <<<"
    )
