import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Native Windows contract",
)


def test_powershell_installer_places_standalone_binary(tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    standalone = tmp_path / "ai-config-source.exe"
    standalone.write_bytes(b"standalone-binary")
    bin_dir = tmp_path / "bin"
    destination = bin_dir / "ai-config.exe"
    bin_dir.mkdir()
    destination.write_bytes(b"old-binary")

    env = os.environ.copy()
    env["AI_CONFIG_BINARY_PATH"] = str(standalone)
    env["AI_CONFIG_BIN_DIR"] = str(bin_dir)
    env["AI_CONFIG_SKIP_PATH_UPDATE"] = "1"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install.ps1"),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert destination.read_bytes() == b"standalone-binary"
    assert "Python" not in result.stdout
