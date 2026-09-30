"""The claude/codex shell functions `acg msg setup` prints, run in real bash."""

import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ai_config import messaging

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the functions are bash; Windows gets its own",
)


@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run one command line through the printed functions; returns each call's argv."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    for tool in ("claude", "codex"):
        fake = bin_dir / tool
        fake.write_text(f'#!/bin/sh\necho "{tool} CODEX_HOME=${{CODEX_HOME:-}} $*" >> "{log}"\n')
        fake.chmod(0o755)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()

    def run(line: str, env: "dict | None" = None) -> list:
        log.unlink(missing_ok=True)
        script = f"{messaging.shell_functions()}\n{line}\n"
        subprocess.run(
            ["bash", "-c", script], cwd=work, check=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home), **(env or {})},
        )
        return log.read_text().splitlines() if log.exists() else []

    run.home, run.work = home, work
    return run


def test_a_new_codex_conversation_opens_in_this_directory(shell) -> None:
    home, work = shell.home, shell.work
    start, tui = shell("codex")
    assert start == f"codex CODEX_HOME={home}/.codex app-server daemon start"
    # daemon 的工作目錄是它啟動時的;沒帶 --cd 的話,新對話會跑到那裡
    assert tui == f"codex CODEX_HOME= --remote unix:// --cd {work}"
    assert shell("codex 修這個 bug")[-1].endswith(f"--remote unix:// --cd {work} 修這個 bug")


def test_codex_keeps_a_directory_the_user_gave(shell) -> None:
    assert shell("codex -C /elsewhere")[-1].endswith("--remote unix:// -C /elsewhere")
    assert shell("codex --cd=/elsewhere")[-1].endswith("--remote unix:// --cd=/elsewhere")


def test_resuming_codex_keeps_the_old_conversations_directory(shell) -> None:
    assert shell("codex resume --last")[-1].endswith("codex CODEX_HOME= resume --remote unix:// --last")
    assert shell("codex fork abc")[-1].endswith("fork --remote unix:// abc")


def test_codex_subcommands_and_explicit_remotes_run_as_typed(shell) -> None:
    assert shell("codex exec hi") == ["codex CODEX_HOME= exec hi"]
    assert shell("codex queue --thread t") == ["codex CODEX_HOME= queue --thread t"]
    assert shell("codex --version") == ["codex CODEX_HOME= --version"]
    assert shell("codex --remote ws://h:1")[-1] == "codex CODEX_HOME= --remote ws://h:1"


def test_codex_attaches_to_the_daemon_of_its_own_home(shell) -> None:
    # unix socket 路徑有長度上限,pytest 的 tmp_path 太深
    other = Path(tempfile.mkdtemp(prefix="acg-sh-", dir="/tmp"))
    sock = other / messaging.SOCKET
    sock.parent.mkdir(parents=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(sock))
        # daemon 已經在跑就不再啟動一次
        calls = shell("codex", env={"CODEX_HOME": str(other)})
    shutil.rmtree(other)
    assert calls == [f"codex CODEX_HOME={other} --remote unix:// --cd {shell.work}"]


def test_claude_gets_the_channel_only_when_interactive(shell) -> None:
    config = Path(messaging.channel_config_path())
    channel = "--dangerously-load-development-channels server:acg"
    assert shell("claude") == [f"claude CODEX_HOME= --mcp-config {config} {channel}"]
    assert shell("claude -p hi") == ["claude CODEX_HOME= -p hi"]
    assert shell("claude mcp list") == ["claude CODEX_HOME= mcp list"]


def test_setup_prints_both_functions_in_one_replaceable_block(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands.msg import run_msg

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert run_msg(["setup"]) == 0
    out = capsys.readouterr().out
    assert out.index("# >>> acg msg >>>") < out.index("claude() {") < out.index("codex() {")
    assert out.index("codex() {") < out.index("# <<< acg msg <<<")
    assert (tmp_path / "ai-config" / "claude-channel.json").is_file()
