import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_config.completion import render_completion


def _bash_executable() -> str:
    if os.name == "nt":
        program_files = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        )
        git_bash = program_files / "Git/bin/bash.exe"
        if git_bash.is_file():
            return str(git_bash)
        pytest.skip("Git Bash is unavailable")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is unavailable")
    return bash


def _bash_candidates(words: list[str]) -> list[str]:
    assignments = " ".join(shlex.quote(word) for word in words)
    command = (
        render_completion("bash")
        + f"\nCOMP_WORDS=({assignments})\n"
        + f"COMP_CWORD={len(words) - 1}\n"
        + "_ai_config_completion\n"
        + "if (( ${#COMPREPLY[@]} )); then printf '%s\\n' \"${COMPREPLY[@]}\"; fi\n"
    )
    result = subprocess.run(
        [_bash_executable(), "-c", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


@pytest.mark.parametrize("executable", ["ai-config", "ai-config.exe", "acg"])
def test_bash_completion_commands(executable: str) -> None:
    assert _bash_candidates([executable, "st"]) == ["status"]


def test_bash_completion_tools_setup_and_shells() -> None:
    assert _bash_candidates(["ai-config", "status", "c"]) == [
        "claude",
        "codex",
    ]
    assert _bash_candidates(["ai-config", "setup", "--r"]) == [
        "--repo-url",
        "--remote-name",
        "--replace-remote",
    ]
    assert _bash_candidates(
        ["ai-config", "setup", "--data-dir", "/tmp/config"]
    ) == []
    assert _bash_candidates(["ai-config", "completion", "p"]) == ["powershell"]
    assert _bash_candidates(["acg", "pu"]) == ["pull", "push"]


def test_completion_command_prints_script() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "completion", "bash"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "complete -o default" in result.stdout


def test_powershell_completion_uses_path_candidates_for_data_dir() -> None:
    script = render_completion("powershell")

    assert "CompletionCompleters]::CompleteFilename" in script
    assert "$previousArgument -eq '--data-dir'" in script


@pytest.mark.skipif(os.name != "nt", reason="Native PowerShell contract")
def test_powershell_completion_script_parses() -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    command = (
        render_completion("powershell")
        + "\n(TabExpansion2 'ai-config st' 12).CompletionMatches.CompletionText"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "status"
