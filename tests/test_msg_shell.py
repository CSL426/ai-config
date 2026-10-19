"""The shell function `acg msg setup` prints, run in real bash."""

import shutil
import subprocess
import sys
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


def test_codex_runs_as_typed_without_the_daemon(shell) -> None:
    """Messages reach Codex through herdr now; the block no longer wraps codex."""
    block = messaging.shell_functions()
    assert "codex()" not in block and "--remote" not in block
    assert shell("codex") == ["codex CODEX_HOME= "]
    assert shell("codex resume --last") == ["codex CODEX_HOME= resume --last"]


def test_plain_claude_is_left_alone_and_claude_msg_loads_the_channel(shell) -> None:
    """The channel asks for confirmation on every start, so only claude-msg carries it."""
    config = Path(messaging.channel_config_path())
    channel = "--dangerously-load-development-channels server:acg"
    assert shell("claude") == ["claude CODEX_HOME= "]
    assert shell("claude-msg -n review") == [f"claude CODEX_HOME= --mcp-config {config} {channel} -n review"]
