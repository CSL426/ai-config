"""The Git Bash launcher install.ps1 writes, as WSL runs it.

WSL appends the Windows PATH, so `acg` typed in WSL runs this script and
then the Windows ai-config.exe. A user there had to press Enter after every
command, saw usage printed as `ai-config`, and had no tab completion.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="runs the script with a POSIX bash",
)


def _launcher(name: str = "acg") -> str:
    """The script install.ps1 writes, rebuilt from its PowerShell literals."""
    text = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    block = re.search(r"\$Lines = @\(\n(.*?)\n    \)", text, re.DOTALL)
    assert block, "install.ps1 no longer builds the launcher from $Lines"
    lines = []
    for raw in block.group(1).splitlines():
        literal = re.fullmatch(r"\s*'(.*)',?", raw)
        assert literal, f"not a single-quoted literal: {raw}"
        lines.append(literal.group(1).replace("''", "'"))
    return ("\n".join(lines) + "\n").replace("__NAME__", name).replace("__EXE__", "ai-config.exe")


@pytest.fixture
def windows_home(tmp_path: Path) -> Path:
    """C:\\Users\\x as WSL sees it: bin/ with the launcher, share/ with the completion."""
    root = tmp_path / "mnt-c-user" / ".local"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "acg").write_text(_launcher(), encoding="utf-8")
    (bin_dir / "acg").chmod(0o755)
    # 假的 Windows 執行檔:印出它實際拿到的環境
    (bin_dir / "ai-config.exe").write_text(
        '#!/usr/bin/env bash\n'
        'echo "entry=$AI_CONFIG_ENTRYPOINT wsl=$AI_CONFIG_WSL wslenv=$WSLENV args=$*"\n',
        encoding="utf-8",
    )
    (bin_dir / "ai-config.exe").chmod(0o755)
    completions = root / "share" / "bash-completion" / "completions"
    completions.mkdir(parents=True)
    (completions / "acg.bash").write_text(
        "complete -o default -W 'status apply' acg\n", encoding="utf-8",
    )
    return root


def _run(windows_home: Path, linux_home: Path, wsl: bool) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], "HOME": str(linux_home)}
    if wsl:
        env |= {"WSL_DISTRO_NAME": "Ubuntu", "WSLENV": "WT_SESSION"}
    return subprocess.run(
        [str(windows_home / "bin" / "acg"), "memory", "push"],
        env=env, capture_output=True, text=True, check=False,
    )


def test_in_wsl_the_name_and_the_wsl_flag_reach_the_windows_program(
    windows_home: Path, tmp_path: Path,
) -> None:
    home = tmp_path / "wsl-home"
    home.mkdir()

    done = _run(windows_home, home, wsl=True)

    assert done.returncode == 0, done.stderr
    assert "entry=acg wsl=1" in done.stdout
    # 使用者原有的 WSLENV 要保留
    assert "wslenv=WT_SESSION:AI_CONFIG_ENTRYPOINT:AI_CONFIG_WSL" in done.stdout
    assert "args=memory push" in done.stdout


def test_wsl_bash_gets_the_completion_once_and_it_loads(
    windows_home: Path, tmp_path: Path,
) -> None:
    home = tmp_path / "wsl-home"
    home.mkdir()
    (home / ".bashrc").write_text("# 使用者原本的設定\n", encoding="utf-8")

    first = _run(windows_home, home, wsl=True)
    second = _run(windows_home, home, wsl=True)

    bashrc = (home / ".bashrc").read_text(encoding="utf-8")
    assert bashrc.startswith("# 使用者原本的設定\n")
    assert bashrc.count(">>> ai-config completion >>>") == 1
    assert "completion" in first.stderr and second.stderr == ""
    loaded = subprocess.run(
        ["bash", "-c", f"source {home / '.bashrc'}; complete -p acg"],
        capture_output=True, text=True, check=False,
    )
    assert "acg" in loaded.stdout, loaded.stderr


def test_outside_wsl_nothing_changes(windows_home: Path, tmp_path: Path) -> None:
    """Git Bash on Windows: no WSLENV to fill and no ~/.bashrc to touch."""
    home = tmp_path / "git-bash-home"
    home.mkdir()

    done = _run(windows_home, home, wsl=False)

    assert "entry=acg wsl= wslenv= " in done.stdout
    assert not (home / ".bashrc").exists()
