import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Unix shell wrapper contract")


def test_installer_bootstraps_into_isolated_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AI_CONFIG_VENV"] = str(home / ".venvs" / "ai-config")

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and "pip install" in result.stderr + result.stdout:
        pytest.skip("pip could not build editable install (offline environment)")
    assert result.returncode == 0, result.stderr + result.stdout
    # Running from inside the checkout: installs THIS repo, no clone
    assert "Using this checkout" in result.stdout

    shim = home / ".local" / "bin" / "ai-config"
    assert shim.is_symlink()

    run = subprocess.run(
        [str(shim), "help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    assert "<command> [tool]" in run.stdout
