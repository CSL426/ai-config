import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_config.completion import COMMANDS, TOOLS, TOOL_COMMANDS, render_completion

EXECUTABLES = ("ai-config", "acg")


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


@pytest.mark.parametrize("executable", EXECUTABLES)
def test_bash_completion_commands(executable: str) -> None:
    assert _bash_candidates([executable, "st"]) == ["status"]


@pytest.mark.parametrize("executable", EXECUTABLES)
@pytest.mark.parametrize("command", COMMANDS)
def test_bash_completion_all_commands_and_flags(
    executable: str,
    command: str,
) -> None:
    assert _bash_candidates([executable, command]) == [command]


@pytest.mark.parametrize("executable", EXECUTABLES)
@pytest.mark.parametrize("command", TOOL_COMMANDS)
def test_bash_completion_all_tool_commands(
    executable: str,
    command: str,
) -> None:
    assert _bash_candidates([executable, command, ""]) == list(TOOLS)


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
    assert _bash_candidates(["ai-config", "ver"]) == ["version"]
    assert _bash_candidates(["acg", "pack"]) == ["package"]
    assert _bash_candidates(["ai-config", "--v"]) == ["--version"]
    assert _bash_candidates(["acg", "-V"]) == ["-V"]
    assert _bash_candidates(["acg", "pull", "a"]) == [
        "agy",
        "all",
        "antigravity",
        "antigravity-cli",
        "antigravity_cli",
    ]
    assert _bash_candidates(["acg", "pull", "claude", ""]) == []


def test_completion_command_prints_script() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "completion", "bash"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "complete -o default" in result.stdout


def test_completion_registration_hides_exe_suffixes() -> None:
    bash_script = render_completion("bash")
    powershell_script = render_completion("powershell")

    assert "ai-config.exe" not in bash_script
    assert "acg.exe" not in bash_script
    assert "'ai-config.exe'" not in powershell_script
    assert "'acg.exe'" not in powershell_script


def test_windows_installer_removes_legacy_exe_completion_file() -> None:
    installer = (
        Path(__file__).resolve().parents[1] / "install.ps1"
    ).read_text(encoding="utf-8")

    assert (
        "Write-Utf8NoBom "
        "(Join-Path $BashCompletionDir 'ai-config.exe.bash')"
        not in installer
    )
    assert "$LegacyExeCompletion" in installer
    assert "Remove-Item -LiteralPath $LegacyExeCompletion" in installer


def test_powershell_completion_uses_path_candidates_for_data_dir() -> None:
    script = render_completion("powershell")

    assert "CompletionCompleters]::CompleteFilename" in script
    assert "$previousArgument -eq '--data-dir'" in script
    for executable in EXECUTABLES:
        assert f"'{executable}'" in script
    assert "'ai-config.exe'" not in script
    assert "'acg.exe'" not in script
    for command in COMMANDS:
        assert f"'{command}'" in script
    for tool in TOOLS:
        assert f"'{tool}'" in script
    assert "$arguments.Count -eq 2" in script


@pytest.mark.skipif(os.name != "nt", reason="Native PowerShell contract")
@pytest.mark.parametrize(
    ("command_line", "cursor", "expected"),
    [
        ("ai-config st", 12, ["status"]),
        ("acg pu", 6, ["pull", "push"]),
        ("acg --v", 7, ["--version"]),
        ("ai-config pull c", 16, ["claude", "codex"]),
        ("ai-config pull claude ", 22, []),
    ],
)
def test_powershell_completion_script_parses(
    command_line: str,
    cursor: int,
    expected: list[str],
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    command = (
        render_completion("powershell")
        + f"\n(TabExpansion2 '{command_line}' {cursor})"
        ".CompletionMatches.CompletionText"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.splitlines() == expected
