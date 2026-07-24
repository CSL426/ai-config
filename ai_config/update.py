"""Self-update: re-run the hosted installer to fetch the latest release.

The installer already owns platform detection, asset naming, staged binary
replacement, and completion refresh — reusing it keeps a single source of
truth for install logic.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from .console import log_error, log_info, log_success, log_warn
from .paths import NATIVE_WINDOWS
from .version import current_version

_DEFAULT_REPOSITORY = "CSL426/ai-config"
_DELEGATED_UPDATE = "AI_CONFIG_UPDATE_DELEGATED"
_RELEASE_VERSION = re.compile(r"^v?(\d+(?:\.\d+){1,3})$")


def _repository() -> str:
    return os.environ.get("AI_CONFIG_TOOL_REPOSITORY", _DEFAULT_REPOSITORY)


def _installer_url(script: str) -> str:
    return f"https://raw.githubusercontent.com/{_repository()}/main/{script}"


def _latest_release_version() -> str:
    url = f"https://api.github.com/repos/{_repository()}/releases/latest"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-config-updater",
        },
    )
    with urlopen(request, timeout=15) as response:
        document = json.load(response)
    tag = document.get("tag_name") if isinstance(document, dict) else None
    if not isinstance(tag, str) or not _RELEASE_VERSION.fullmatch(tag):
        raise RuntimeError("Latest GitHub release has an invalid version tag")
    return tag.removeprefix("v")


def _version_key(value: str) -> "tuple[int, ...] | None":
    match = _RELEASE_VERSION.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _is_up_to_date(current: str, latest: str) -> bool:
    current_key = _version_key(current)
    latest_key = _version_key(latest)
    if current_key is None or latest_key is None:
        return current == latest
    width = max(len(current_key), len(latest_key))
    return current_key + (0,) * (width - len(current_key)) >= (
        latest_key + (0,) * (width - len(latest_key))
    )


def _standalone_candidate() -> Path:
    executable = "ai-config.exe" if NATIVE_WINDOWS else "ai-config"
    default_bin = Path.home() / ".local" / "bin"
    return Path(os.environ.get("AI_CONFIG_BIN_DIR", default_bin)) / executable


def _delegate_source_update() -> "int | None":
    if os.environ.get(_DELEGATED_UPDATE) == "1":
        return None
    candidate = _standalone_candidate()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    try:
        if candidate.resolve() == Path(sys.argv[0]).resolve():
            return None
    except OSError:
        return None

    environment = os.environ.copy()
    environment[_DELEGATED_UPDATE] = "1"
    log_info(f"Delegating update to standalone release: {candidate}")
    completed = subprocess.run(
        [str(candidate), "update"],
        env=environment,
        check=False,
    )
    return completed.returncode


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_update_script(parent_pid: int) -> str:
    installer_url = _powershell_literal(_installer_url("install.ps1"))
    return "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            (
                f"Wait-Process -Id {parent_pid} "
                "-ErrorAction SilentlyContinue"
            ),
            (
                "$installer = Join-Path ([IO.Path]::GetTempPath()) "
                "('install-ai-config-' + "
                "[guid]::NewGuid().ToString('N') + '.ps1')"
            ),
            "try {",
            (
                "  Invoke-WebRequest -UseBasicParsing "
                f"-Uri {installer_url} -OutFile $installer"
            ),
            "  & $installer",
            "  exit $LASTEXITCODE",
            "}",
            "finally {",
            (
                "  Remove-Item -LiteralPath $installer -Force "
                "-ErrorAction SilentlyContinue"
            ),
            "}",
        )
    )


def _launch_windows_update() -> int:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _windows_update_script(os.getpid()),
    ]
    try:
        subprocess.Popen(
            command,
            creationflags=getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            ),
        )
    except OSError as exc:
        log_error(f"Could not start the PowerShell updater: {exc}")
        return 1
    log_success(
        "Update handed off to PowerShell; installation will continue "
        "after this process exits"
    )
    return 0


def run_update() -> int:
    if not getattr(sys, "frozen", False):
        delegated = _delegate_source_update()
        if delegated is not None:
            return delegated
        log_error(
            "This ai-config runs from source, not a standalone release."
        )
        log_info(
            "Update the checkout with: git pull "
            "(then `pip install -e .` if the package metadata changed)"
        )
        return 1

    current = current_version()
    try:
        latest = _latest_release_version()
    except Exception as exc:  # noqa: BLE001 - top-level guard must not crash
        log_error(f"Could not check the latest release version: {exc}")
        return 1
    if current is None:
        log_warn("Current standalone version is unavailable; updating once")
    else:
        log_info(f"Current version: {current}; latest release: {latest}")
        if _is_up_to_date(current, latest):
            log_success("ai-config is already up to date")
            return 0

    if NATIVE_WINDOWS:
        return _launch_windows_update()

    url = _installer_url("install.sh")
    log_info(f"Fetching installer from {url}")
    completed = subprocess.run(
        ["bash", "-c", f'curl -fsSL "{url}" | bash'],
        check=False,
    )
    if completed.returncode != 0:
        log_error("Update failed; the current binary is unchanged")
    return completed.returncode
