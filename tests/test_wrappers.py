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
        "#!/bin/sh\nprintf 'standalone %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    standalone.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AI_CONFIG_BINARY_PATH"] = str(standalone)
    env["AI_CONFIG_BIN_DIR"] = str(home / ".local" / "bin")
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

    executable = home / ".local" / "bin" / "ai-config"
    assert executable.is_file()
    assert not executable.is_symlink()
    assert old_target.read_text(encoding="utf-8") == "old install\n"
    assert os.access(executable, os.X_OK)

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
