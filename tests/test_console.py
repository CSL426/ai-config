import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_console_reconfigures_legacy_output_encoding() -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ai_config.console import log_header; log_header('test')",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert "═══ test ═══" in result.stdout.decode("utf-8")
