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
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from ..console import log_error, log_info, log_success, log_warn
from ..paths import ENTRYPOINT, NATIVE_WINDOWS
from ..version import current_version

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


def _delegate_source_update(tag: "str | None" = None) -> "int | None":
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
        [str(candidate), "update", *([tag] if tag else [])],
        env=environment,
        check=False,
    )
    return completed.returncode


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_VERSION_PATTERN = re.compile(r"^v?\d+(\.\d+)*$")


def normalize_version(requested: str) -> "str | None":
    """Return the release tag for a requested version, or None if malformed."""
    candidate = requested.strip()
    if not _VERSION_PATTERN.match(candidate):
        return None
    return candidate if candidate.startswith("v") else f"v{candidate}"


def _windows_update_script(parent_pid: int, tag: "str | None" = None) -> str:
    installer_url = _powershell_literal(_installer_url("install.ps1"))
    pin = (
        f"$env:AI_CONFIG_VERSION = {_powershell_literal(tag)}"
        if tag
        else "# no pinned version"
    )
    return "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            pin,
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
            # A terminal line lets a reader tell "finished" from "still running".
            (
                "  if ($LASTEXITCODE -eq 0) { "
                "Write-Output 'ai-config update: finished successfully' } "
                "else { Write-Output "
                "\"ai-config update: FAILED (exit $LASTEXITCODE)\" }"
            ),
            "  exit $LASTEXITCODE",
            "}",
            "catch {",
            "  Write-Output \"ai-config update: FAILED ($($_.Exception.Message))\"",
            "  exit 1",
            "}",
            "finally {",
            (
                "  Remove-Item -LiteralPath $installer -Force "
                "-ErrorAction SilentlyContinue"
            ),
            "}",
        )
    )


def _spawn_windows_updater(command: list, output) -> None:
    subprocess.Popen(
        command,
        stdout=output,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _launch_windows_update(tag: "str | None" = None) -> int:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _windows_update_script(os.getpid(), tag),
    ]
    # The handoff outlives this process, so its output must not go to the shared
    # console: the shell has already redrawn its prompt by then, and installer
    # lines would land on top of it looking like a crash. Log to a file instead.
    log_path = Path(tempfile.gettempdir()) / "ai-config-update.log"
    try:
        # The child inherits the handle, so closing it here is safe and correct.
        with open(log_path, "w", encoding="utf-8") as log_file:
            _spawn_windows_updater(command, log_file)
    except OSError as exc:
        log_error(f"Could not start the PowerShell updater: {exc}")
        return 1
    log_success("Update handed off to PowerShell; it continues in the background")
    log_info(
        "This process must exit first so Windows releases the lock on the "
        "running executable, so there is no progress bar here"
    )
    log_info(f"It usually takes a few seconds. Progress: {log_path}")
    log_info(f"Confirm it finished with: {ENTRYPOINT} version")
    return 0


def run_update(requested_version: "str | None" = None) -> int:
    tag = None
    if requested_version is not None:
        tag = normalize_version(requested_version)
        if tag is None:
            log_error(f"Not a valid version: {requested_version}")
            log_info("Expected a release version such as 1.0.13 or v1.0.13")
            return 1

    if not getattr(sys, "frozen", False):
        delegated = _delegate_source_update(tag)
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
    if tag is not None:
        # A pinned version is an explicit instruction, including a downgrade, so
        # the latest-release comparison is skipped entirely.
        log_info(f"Current version: {current or 'unknown'}; installing {tag}")
    else:
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
        return _launch_windows_update(tag)

    url = _installer_url("install.sh")
    log_info(f"Fetching installer from {url}")
    environment = os.environ.copy()
    if tag is not None:
        environment["AI_CONFIG_VERSION"] = tag
    completed = subprocess.run(
        ["bash", "-c", f'curl -fsSL "{url}" | bash'],
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        log_error("Update failed; the current binary is unchanged")
    return completed.returncode


# ─── 被動更新提示 ─────────────────────────────────────────────
# 一般指令執行時不打網路:只讀快取,過期就派一個分離的背景行程更新快取,
# 下一次指令才會看到提示。AI_CONFIG_NO_UPDATE_CHECK=1 可完全關閉。

_CHECK_INTERVAL_SECONDS = 24 * 60 * 60


def _update_check_cache_path() -> Path:
    from ..config import config_path

    return config_path().parent / "update-check.json"


def _read_update_check_cache() -> "dict | None":
    try:
        data = json.loads(
            _update_check_cache_path().read_text(encoding="utf-8")
        )
        return data if isinstance(data, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def run_update_check_refresh() -> int:
    """背景行程進入點(隱藏命令 __update-check):抓最新版號寫入快取。"""
    import time

    try:
        latest = _latest_release_version()
    except Exception:  # noqa: BLE001 — 背景檢查失敗必須完全安靜
        return 0
    path = _update_check_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"checked_at": int(time.time()), "latest": latest}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return 0


def _spawn_update_check() -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "__update-check"]
    else:
        command = [sys.executable, "-m", "ai_config", "__update-check"]
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if NATIVE_WINDOWS:
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except OSError:
        pass


def maybe_notify_update() -> None:
    """讀快取提示新版;過期則派背景行程刷新。零網路呼叫、零阻塞。"""
    import time

    if (
        os.environ.get("AI_CONFIG_NO_UPDATE_CHECK")
        or "PYTEST_CURRENT_TEST" in os.environ
        or not sys.stdout.isatty()
    ):
        return
    cache = _read_update_check_cache()
    current = current_version()
    if (
        cache
        and current
        and isinstance(cache.get("latest"), str)
        and not _is_up_to_date(current, cache["latest"])
    ):
        log_info(
            f"新版 v{cache['latest']} 可用(目前 v{current}),"
            f"執行 {ENTRYPOINT} update 更新"
        )
    checked_at = cache.get("checked_at", 0) if cache else 0
    if time.time() - checked_at >= _CHECK_INTERVAL_SECONDS:
        _spawn_update_check()
