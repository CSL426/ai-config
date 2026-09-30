"""acg msg pieces that must work on Windows too: processes, the codex proxy, the pipe channel."""

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ai_config import channel, messaging, processes

# 模擬 `codex app-server proxy`:在 stdin/stdout 上直接扮演 daemon 的 WebSocket
_FAKE_PROXY = textwrap.dedent('''
    import base64, hashlib, json, struct, sys
    read, write = sys.stdin.buffer, sys.stdout.buffer
    head = b""
    while b"\\r\\n\\r\\n" not in head:
        chunk = read.read1(4096)
        if not chunk:
            sys.exit(0)
        head += chunk
    key = next(line.split(b":", 1)[1].strip() for line in head.split(b"\\r\\n")
               if line.lower().startswith(b"sec-websocket-key"))
    accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    write.write(b"HTTP/1.1 101 Switching Protocols\\r\\nSec-WebSocket-Accept: " + accept + b"\\r\\n\\r\\n")

    def send(message):
        data = json.dumps(message).encode()
        write.write(bytes([0x81, 126]) + struct.pack(">H", len(data)) + data)
        write.flush()

    buffer = head.split(b"\\r\\n\\r\\n", 1)[1]
    def need(n):
        global buffer
        while len(buffer) < n:
            chunk = read.read1(65536)
            if not chunk:
                sys.exit(0)
            buffer += chunk
        out, buffer = buffer[:n], buffer[n:]
        return out

    send({"jsonrpc": "2.0", "method": "remoteControl/status/changed"})
    while True:
        _first, second = need(2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", need(2))[0]
        mask = need(4)
        message = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(need(size))))
        if "id" not in message:
            continue
        result = {}
        if message["method"] == "thread/loaded/list":
            result = {"data": ["t-main"]}
        elif message["method"] == "thread/read":
            result = {"thread": {"name": "審查", "cwd": "C:/work", "status": {"type": "idle"},
                                 "source": "cli"}}
        send({"jsonrpc": "2.0", "id": message["id"], "result": result})
''')


def test_a_live_process_and_its_parent_are_found() -> None:
    assert processes.pid_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not processes.pid_alive(child.pid)
    assert not processes.pid_alive(0)
    if sys.platform != "darwin":  # macOS 沒有 /proc,也還沒支援
        assert processes.parent_pid(os.getpid()) == os.getppid()


def test_codex_sessions_are_listed_through_the_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = tmp_path / "proxy.py"
    fake.write_text(_FAKE_PROXY, encoding="utf-8")
    seen = []

    def argv(path: Path) -> list:
        seen.append(path)
        return [sys.executable, str(fake)]

    monkeypatch.setattr(messaging, "HOME", tmp_path)
    monkeypatch.setattr(messaging, "_VIA_PROXY", True)
    monkeypatch.setattr(messaging, "_proxy_argv", argv)
    sock = messaging.socket_path(tmp_path / ".codex")
    sock.parent.mkdir(parents=True)
    sock.write_bytes(b"")

    peers = messaging.codex_peers()

    assert [(p.id, p.name, p.account) for p in peers] == [("t-main", "審查", "default")]
    assert seen == [sock]


def test_a_proxy_that_exits_is_a_clean_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(messaging, "_VIA_PROXY", True)
    monkeypatch.setattr(messaging, "_proxy_argv", lambda _path: [sys.executable, "-c", "pass"])

    # 先寫進去再讀到 EOF,或寫的時候 pipe 已經斷了,都要是能接住的錯誤,不是卡住
    with pytest.raises((OSError, messaging.MessagingError)):
        messaging.CodexDaemon(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="the channel uses a named pipe on Windows only")
def test_a_message_reaches_a_windows_channel_through_its_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    out = io.StringIO()
    server = channel.Channel(out)
    path = messaging.channel_socket(4321)
    server.listen(path)
    peer = messaging.Peer("claude", "s-1", "acg", "", "", "idle", pid=4321)
    try:
        assert path.read_text(encoding="utf-8").startswith("\\\\.\\pipe\\acg-msg-4321-")
        messaging.send_claude(peer, "codex 審查", "看完了,沒問題")
        assert messaging._send_pipe(path.read_text(encoding="utf-8"), b"not json") == b"error"
    finally:
        server.close()

    [event] = [json.loads(line) for line in out.getvalue().splitlines()]
    assert event["params"] == {"content": "看完了,沒問題", "meta": {"from": "codex 審查"}}
    assert not path.exists(), "結束時要把 pipe 名稱檔收掉"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_a_pipe_name_file_pointing_elsewhere_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = messaging.channel_socket(4321)
    path.parent.mkdir(parents=True)
    path.write_text(r"C:\somewhere\else", encoding="utf-8")
    peer = messaging.Peer("claude", "s-1", "acg", "", "", "idle", pid=4321)

    with pytest.raises(messaging.MessagingError, match="送不進"):
        messaging.send_claude(peer, "a", "b")


@pytest.mark.skipif(sys.platform != "win32", reason="the PowerShell functions are for Windows")
def test_the_powershell_functions_attach_like_the_bash_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    assert powershell
    bin_dir, work, codex_home = tmp_path / "bin", tmp_path / "work", tmp_path / "codex"
    bin_dir.mkdir()
    work.mkdir()
    log = tmp_path / "calls.txt"
    for tool in ("claude", "codex"):
        (bin_dir / f"{tool}.cmd").write_text(
            f'@echo {tool} CODEX_HOME=%CODEX_HOME% %*>>"{log}"\r\n', encoding="ascii")

    def run(line: str, env: "dict | None" = None) -> list:
        log.unlink(missing_ok=True)
        script = f"{messaging.powershell_functions()}\n{line}\n"
        subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script], cwd=work, check=True,
            env={**os.environ, "PATH": f"{bin_dir};{os.environ['PATH']}", "CODEX_HOME": "", **(env or {})},
        )
        return [row.strip() for row in log.read_text().splitlines()] if log.exists() else []

    start, tui = run("codex", {"CODEX_HOME": str(codex_home)})
    assert start == f"codex CODEX_HOME={codex_home} app-server daemon start"
    assert tui == f"codex CODEX_HOME={codex_home} --remote unix:// --cd {work}"
    # -c 是設定,不是 -C 目錄
    assert run("codex -c model=o3")[-1].endswith(f"--remote unix:// --cd {work} -c model=o3")
    assert run("codex -C D:\\x")[-1].endswith("--remote unix:// -C D:\\x")
    assert run("codex resume --last")[-1].endswith("resume --remote unix:// --last")
    assert run("codex exec hi") == ["codex CODEX_HOME= exec hi"]

    sock = codex_home / messaging.SOCKET
    sock.parent.mkdir(parents=True)
    sock.write_bytes(b"")
    assert run("codex", {"CODEX_HOME": str(codex_home)}) == [
        f"codex CODEX_HOME={codex_home} --remote unix:// --cd {work}"]

    config = messaging.channel_config_path()
    assert run("claude") == [
        f"claude CODEX_HOME= --mcp-config {config} --dangerously-load-development-channels server:acg"]
    assert run("claude -p hi") == ["claude CODEX_HOME= -p hi"]
