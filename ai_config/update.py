"""Self-update: re-run the hosted installer to fetch the latest release.

The installer already owns platform detection, asset naming, staged binary
replacement, and completion refresh — reusing it keeps a single source of
truth for install logic.
"""

import os
import subprocess
import sys

from .console import log_error, log_info
from .paths import NATIVE_WINDOWS

_DEFAULT_REPOSITORY = "CSL426/ai-config"


def _repository() -> str:
    return os.environ.get("AI_CONFIG_TOOL_REPOSITORY", _DEFAULT_REPOSITORY)


def _installer_url(script: str) -> str:
    return f"https://raw.githubusercontent.com/{_repository()}/main/{script}"


def run_update() -> int:
    if not getattr(sys, "frozen", False):
        log_error(
            "This ai-config runs from source, not a standalone release."
        )
        log_info(
            "Update the checkout with: git pull "
            "(then `pip install -e .` if the package metadata changed)"
        )
        return 1

    if NATIVE_WINDOWS:
        # Windows cannot replace a running executable from inside itself.
        log_info("Run this in PowerShell to update:")
        print(
            "  powershell -NoProfile -ExecutionPolicy Bypass -Command "
            f'"iwr {_installer_url("install.ps1")} -OutFile '
            '$env:TEMP\\install-ai-config.ps1; '
            '& $env:TEMP\\install-ai-config.ps1"'
        )
        return 1

    url = _installer_url("install.sh")
    log_info(f"Fetching installer from {url}")
    completed = subprocess.run(
        ["bash", "-c", f'curl -fsSL "{url}" | bash']
    )
    if completed.returncode != 0:
        log_error("Update failed; the current binary is unchanged")
    return completed.returncode
