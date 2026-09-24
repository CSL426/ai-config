"""The Claude side of acg msg: a channel that turns deliveries into events."""

import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

from ai_config import channel, messaging, paths

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX") or sys.platform == "win32",
    reason="the channel listens on a unix socket",
)


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> Path:
    # AF_UNIX 路徑有長度上限,pytest 的 tmp_path 太深
    root = Path(tempfile.mkdtemp(prefix="acg-ch-", dir="/tmp"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    return root


def _events(out: io.StringIO) -> list:
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_handshake_declares_the_channel_and_nothing_else() -> None:
    out = io.StringIO()
    server = channel.Channel(out)

    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18"}})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})

    first, second = _events(out)
    assert first["result"]["capabilities"] == {"experimental": {"claude/channel": {}}}
    assert first["result"]["protocolVersion"] == "2025-06-18"
    # 訊息來自別的 AI,不是使用者;這句一定要在
    assert "not from the user" in first["result"]["instructions"]
    assert second == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_a_delivered_message_becomes_a_channel_event(state: Path) -> None:
    out = io.StringIO()
    server = channel.Channel(out)
    server.listen(messaging.channel_socket(4321))
    peer = messaging.Peer("claude", "s-1", "acg", "", "", "idle", pid=4321)
    try:
        messaging.send_claude(peer, "codex 審查", "看完了,沒問題")
    finally:
        server.close()

    [event] = _events(out)
    assert event["method"] == "notifications/claude/channel"
    assert event["params"] == {"content": "看完了,沒問題", "meta": {"from": "codex 審查"}}
    assert not messaging.channel_socket(4321).exists(), "結束時要把 socket 收掉"


def test_garbage_on_the_socket_is_refused_not_pushed(state: Path) -> None:
    out = io.StringIO()
    server = channel.Channel(out)
    path = messaging.channel_socket(99)
    server.listen(path)
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.connect(str(path))
            conn.sendall(b"not json\n")
            assert conn.recv(16).strip() == b"error"
    finally:
        server.close()
    assert out.getvalue() == ""


def test_claude_sessions_are_listed_with_whether_they_can_receive(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    (home / "sessions").mkdir(parents=True)
    me = os.getpid()
    (home / "sessions" / f"{me}.json").write_text(json.dumps({
        "pid": me, "sessionId": "s-live", "name": "acg", "cwd": "/w",
        "status": "idle", "kind": "interactive"}))
    (home / "sessions" / "999999.json").write_text(json.dumps({
        "pid": 999999, "sessionId": "s-dead", "name": "gone", "kind": "interactive"}))
    monkeypatch.setattr(paths, "CLAUDE_HOME", home)

    [peer] = messaging.claude_peers()
    assert (peer.id, peer.name) == ("s-live", "acg")
    assert "channel" in peer.unreachable

    messaging.channel_socket(me).parent.mkdir(parents=True)
    messaging.channel_socket(me).write_text("")
    assert messaging.claude_peers()[0].unreachable == ""


def test_the_reply_line_names_the_recipient_so_its_answer_has_a_sender() -> None:
    body, tag = messaging.compose("幫我看", "acg", reply_as="審查")

    assert f"#{tag}" in body
    assert "acg msg send acg \"<內容>\" --from '審查'" in body
    assert "--from" not in messaging.compose("x", "acg")[0]


def test_a_socket_left_by_a_killed_session_is_cleared(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    (home / "sessions").mkdir(parents=True)
    monkeypatch.setattr(paths, "CLAUDE_HOME", home)
    stale = messaging.channel_socket(999999)
    stale.parent.mkdir(parents=True)
    stale.write_text("")

    messaging.claude_peers()

    assert not stale.exists()


def test_a_terminal_closing_still_removes_the_socket(state: Path) -> None:
    import signal
    import subprocess

    script = (
        "import os, sys, time\n"
        "from ai_config import channel, messaging\n"
        "channel._claude_pid = lambda: 4242\n"
        "sys.exit(channel.run())\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdin=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    path = messaging.channel_socket(4242)
    for _ in range(100):
        if path.exists():
            break
        import time
        time.sleep(0.05)
    assert path.exists()
    proc.send_signal(signal.SIGHUP)
    proc.wait(timeout=10)
    assert not path.exists()
