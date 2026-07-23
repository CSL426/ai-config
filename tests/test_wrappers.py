import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Unix shell wrapper contract")


def test_installer_places_standalone_binary_without_python(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    standalone = tmp_path / "standalone-ai-config"
    standalone.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = list ]; then\n"
        "    exit \"${AI_CONFIG_TEST_LIST_EXIT:-1}\"\n"
        "fi\n"
        "printf 'standalone %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    standalone.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AI_CONFIG_BINARY_PATH"] = str(standalone)
    env["AI_CONFIG_BIN_DIR"] = str(home / ".local" / "bin")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    old_target = home / "old-venv" / "ai-config"
    old_target.parent.mkdir()
    old_target.write_text("old install\n", encoding="utf-8")
    destination = home / ".local" / "bin" / "ai-config"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(old_target)

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "Installing local standalone binary" in result.stdout
    assert "ai-config setup" in result.stdout
    assert "Update complete" in result.stdout

    executable = home / ".local" / "bin" / "ai-config"
    assert executable.is_file()
    assert not executable.is_symlink()
    assert old_target.read_text(encoding="utf-8") == "old install\n"
    assert os.access(executable, os.X_OK)
    acg = home / ".local" / "bin" / "acg"
    assert acg.is_symlink()
    assert acg.readlink() == Path("ai-config")
    completion = home / ".local/share/bash-completion/completions/ai-config.bash"
    acg_completion = home / ".local/share/bash-completion/completions/acg.bash"
    assert completion.read_text(encoding="utf-8") == "standalone completion bash\n"
    assert acg_completion.read_text(encoding="utf-8") == (
        "standalone completion bash\n"
    )
    assert "Activate in this shell" in result.stdout
    assert "hash -r && source" in result.stdout

    run = subprocess.run(
        [str(executable), "help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    assert "standalone help" in run.stdout

    acg_run = subprocess.run(
        [str(acg), "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert acg_run.returncode == 0, acg_run.stderr + acg_run.stdout
    assert "standalone status" in acg_run.stdout

    env["AI_CONFIG_TEST_LIST_EXIT"] = "0"
    repeated = subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr + repeated.stdout
    assert "Update complete" in repeated.stdout
    assert "existing data repository configuration preserved" in repeated.stdout
    assert "Starting first-run setup" not in repeated.stdout
    assert completion.read_text(encoding="utf-8") == "standalone completion bash\n"
    assert acg_completion.read_text(encoding="utf-8") == (
        "standalone completion bash\n"
    )


@pytest.mark.parametrize(
    "platform",
    ["MINGW64_NT-10.0-26200", "MSYS_NT-10.0", "CYGWIN_NT-10.0"],
)
def test_installer_delegates_windows_posix_shell_to_powershell(
    tmp_path: Path,
    platform: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "powershell-calls.txt"
    scripts = {
        "uname": """#!/usr/bin/env bash
if [[ "$1" == "-s" ]]; then
    printf '%s\\n' "$AI_CONFIG_TEST_PLATFORM"
else
    printf 'x86_64\\n'
fi
""",
        "curl": """#!/usr/bin/env bash
while (( $# )); do
    if [[ "$1" == "--output" ]]; then
        printf 'fake PowerShell installer\\n' > "$2"
        exit
    fi
    shift
done
exit 1
""",
        "cygpath": """#!/usr/bin/env bash
printf 'C:\\\\Temp\\\\install-ai-config.ps1\\n'
""",
        "powershell.exe": """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$AI_CONFIG_POWERSHELL_CALLS"
exit "${AI_CONFIG_POWERSHELL_EXIT_CODE:-0}"
""",
    }
    for name, content in scripts.items():
        script = fake_bin / name
        script.write_text(content, encoding="utf-8")
        script.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AI_CONFIG_POWERSHELL_CALLS"] = str(calls)
    env["AI_CONFIG_TEST_PLATFORM"] = platform

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Windows POSIX shell detected" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\Temp\install-ai-config.ps1",
    ]

    env["AI_CONFIG_POWERSHELL_EXIT_CODE"] = "23"
    failed = subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 23
