"""acg msg: listing and reaching live Codex sessions through their daemon."""

import base64
import hashlib
import json
import socket
import struct
import sys
import threading
from pathlib import Path

import pytest

from ai_config import messaging

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX") or sys.platform == "win32",
    reason="the Codex daemon listens on a unix socket",
)

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeDaemon:
    """A WebSocket JSON-RPC server that answers like Codex's app-server."""

    def __init__(self, path: Path, threads: dict, turns: "list | None" = None) -> None:
        self.threads = threads
        self.turns = turns or []
        self.calls: list = []
        path.parent.mkdir(parents=True, exist_ok=True)
        self._server = socket.socket(socket.AF_UNIX)
        self._server.bind(str(path))
        self._server.listen()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        with conn:
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(4096)
            key = next(
                line.split(b":", 1)[1].strip() for line in head.split(b"\r\n")
                if line.lower().startswith(b"sec-websocket-key")
            )
            accept = base64.b64encode(hashlib.sha1(key + _GUID.encode()).digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                         b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            # 真的 daemon 一連上就先推通知;客戶端要能跳過它
            self._send(conn, {"jsonrpc": "2.0", "method": "remoteControl/status/changed"})
            buffer = b""
            while True:
                try:
                    message, buffer = self._frame(conn, buffer)
                except (ConnectionError, OSError):
                    return
                if message is None:
                    return
                self.calls.append(message.get("method"))
                if "id" in message:
                    self._send(conn, {"jsonrpc": "2.0", "id": message["id"],
                                      "result": self._answer(message)})

    def _answer(self, message: dict) -> dict:
        method, params = message["method"], message.get("params", {})
        if method == "thread/loaded/list":
            return {"data": list(self.threads)}
        if method == "thread/read":
            return {"thread": self.threads[params["threadId"]]}
        if method == "thread/turns/list":
            return {"data": self.turns}
        return {}

    @staticmethod
    def _frame(conn: socket.socket, buffer: bytes):
        def need(n):
            nonlocal buffer
            while len(buffer) < n:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError
                buffer += chunk
            out, buffer = buffer[:n], buffer[n:]
            return out
        _first, second = need(2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", need(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", need(8))[0]
        mask = need(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(need(size)))
        return json.loads(payload), buffer

    @staticmethod
    def _send(conn: socket.socket, message: dict) -> None:
        data = json.dumps(message).encode()
        head = bytes([0x81, len(data)]) if len(data) < 126 else (
            bytes([0x81, 126]) + struct.pack(">H", len(data)))
        conn.sendall(head + data)

    def close(self) -> None:
        self._server.close()


def _thread(name: str, source: object = "cli") -> dict:
    return {"name": name, "cwd": "/work", "status": {"type": "idle"}, "source": source}


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch) -> Path:
    # AF_UNIX 路徑有長度上限,pytest 的 tmp_path 太深
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="acg-msg-", dir="/tmp"))
    monkeypatch.setattr(messaging, "HOME", root)
    return root


def test_lists_live_sessions_and_leaves_out_sub_agents(home: Path) -> None:
    daemon = FakeDaemon(messaging.socket_path(home / ".codex-set"), {
        "t-main": _thread("審查"),
        "t-sub": _thread("", {"subAgent": {"thread_spawn": {}}}),
    })
    try:
        peers = messaging.list_peers()
    finally:
        daemon.close()

    assert [(p.id, p.name, p.account) for p in peers] == [("t-main", "審查", "set")]


def test_a_dead_socket_does_not_break_the_list(home: Path) -> None:
    stale = messaging.socket_path(home / ".codex-csl")
    stale.parent.mkdir(parents=True)
    stale.write_text("")  # 留下的檔案,沒有 daemon 在聽

    assert messaging.list_peers() == []


def test_a_name_that_matches_two_sessions_is_not_guessed() -> None:
    peers = [
        messaging.Peer("codex", "01a0-aaaa-1111", "審查", "set", "", "idle"),
        messaging.Peer("codex", "01a0-bbbb-2222", "審查", "csl", "", "idle"),
    ]
    with pytest.raises(messaging.MessagingError, match="不只一個"):
        messaging.resolve("審查", peers)
    assert messaging.resolve("01a0-bbbb", peers).account == "csl"


def test_the_reply_is_read_from_the_turn_the_message_started(home: Path) -> None:
    turns = [
        {"status": "completed", "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": "[acg 訊息 #abc123,來自 x]\n嗨"}]},
            {"type": "agentMessage", "text": "想一下", "phase": "commentary"},
            {"type": "agentMessage", "text": "收到", "phase": "finalAnswer"},
        ]},
        {"status": "completed", "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": "別人的"}]},
            {"type": "agentMessage", "text": "不是這個"},
        ]},
    ]
    daemon = FakeDaemon(messaging.socket_path(home / ".codex-set"), {"t": _thread("a")}, turns)
    try:
        peer = messaging.resolve("a")
        assert messaging.wait_codex_reply(peer, "abc123", timeout=5, poll=0.1) == "收到"
    finally:
        daemon.close()


def test_a_failed_turn_says_why(home: Path) -> None:
    turns = [{"status": "failed", "error": {"message": "usage limit, try again at 5:04 PM"},
              "items": [{"type": "userMessage", "content": [{"type": "text", "text": "#abc123"}]}]}]
    daemon = FakeDaemon(messaging.socket_path(home / ".codex-set"), {"t": _thread("a")}, turns)
    try:
        peer = messaging.resolve("a")
        with pytest.raises(messaging.MessagingError, match="送達.*5:04 PM"):
            messaging.wait_codex_reply(peer, "abc123", timeout=5, poll=0.1)
    finally:
        daemon.close()


def test_send_queues_into_the_account_the_session_runs_under(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = FakeDaemon(messaging.socket_path(home / ".codex-csl"), {"t-9": _thread("修 bug")})
    ran = {}

    class Done:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kwargs):
        ran["cmd"], ran["env"] = cmd, kwargs["env"]
        return Done()

    monkeypatch.setattr(messaging.subprocess, "run", fake_run)
    monkeypatch.setattr(messaging, "_codex_binary", lambda: "codex")
    monkeypatch.setenv("ACG_MSG_FROM", "acg-main")
    try:
        peer, reply = messaging.send("修 bug", "幫我看一下")
    finally:
        daemon.close()

    assert peer.id == "t-9" and reply == ""
    assert ran["env"]["CODEX_HOME"] == str(home / ".codex-csl")
    assert ran["cmd"][:2] == ["codex", "queue"]
    assert "--thread" in ran["cmd"] and "t-9" in ran["cmd"]
    body = ran["cmd"][ran["cmd"].index("--message") + 1]
    assert "來自 acg-main" in body and "幫我看一下" in body


def test_an_empty_message_is_refused() -> None:
    with pytest.raises(messaging.MessagingError, match="空"):
        messaging.send("誰", "  ")


def test_the_command_lists_and_reports_errors(
    home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands.msg import run_msg

    assert run_msg(["list"]) == 0
    assert "沒有找到" in capsys.readouterr().out
    assert run_msg(["send", "不存在", "嗨"]) == 1
    assert run_msg(["bogus"]) == 1
