"""Keep the /acg skill in Claude Code: install it when missing, update it when present.

Installing acg used to leave the skill as two more commands to type, so a
machine had the CLI and no /acg until someone remembered. Best effort
throughout: a machine without Claude Code, or one Claude refuses, is not
an install or update failure.

On a machine that belongs to someone else, installing a user-scope plugin
changes the owner's Claude Code. AI_CONFIG_NO_PLUGIN=1 skips the whole step.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from .console import log_info, log_success, log_warn
from .subproc import UTF8

PLUGIN = "acg@acg"
MARKETPLACE = "acg"
OPT_OUT = "AI_CONFIG_NO_PLUGIN"


def claude_binary() -> "str | None":
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.is_file() else None


def _run(claude: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [claude, "plugin", *args],
        capture_output=True, text=True, **UTF8, timeout=180, check=False,
    )


def _last_line(done: subprocess.CompletedProcess) -> str:
    lines = (done.stdout or done.stderr or "").strip().splitlines()
    return lines[-1] if lines else ""


def _listed(claude: str, *args: str) -> "list | None":
    """A `--json` listing, or None when this Claude Code cannot give one."""
    done = _run(claude, *args, "--json")
    if done.returncode != 0:
        return None
    try:
        value = json.loads(done.stdout)
    except ValueError:
        return None
    return value if isinstance(value, list) else None


def _installed(claude: str) -> "bool | None":
    # 專案範圍也可能裝著一份;要看的是跟著這個使用者走的那份
    plugins = _listed(claude, "list")
    if plugins is None:
        return None
    return any(
        isinstance(p, dict) and p.get("id") == PLUGIN and p.get("scope") == "user"
        for p in plugins
    )


def _marketplace_known(claude: str) -> bool:
    markets = _listed(claude, "marketplace", "list") or []
    return any(isinstance(m, dict) and m.get("name") == MARKETPLACE for m in markets)


def _update(claude: str) -> None:
    done = _run(claude, "update", PLUGIN)
    last = _last_line(done)
    if done.returncode == 0:
        log_info(f"plugin:{last}" if last else "plugin 已是最新")
    else:
        log_warn(f"plugin 沒有更新:{last or f'exit {done.returncode}'}")


def _install(claude: str, repository: str) -> None:
    if not _marketplace_known(claude):
        added = _run(claude, "marketplace", "add", repository)
        if added.returncode != 0:
            log_warn(f"沒有裝上 /acg:{_last_line(added) or 'marketplace add 失敗'}")
            return
    done = _run(claude, "install", PLUGIN, "--scope", "user")
    if done.returncode != 0:
        log_warn(f"沒有裝上 /acg:{_last_line(done) or f'exit {done.returncode}'}")
        return
    log_success("已在 Claude Code 裝上 /acg;重開 Claude Code 後用 /acg <子指令>")


def ensure(repository: str) -> None:
    if os.environ.get(OPT_OUT):
        log_info(f"{OPT_OUT} 已設定,略過 Claude Code 的 /acg")
        return
    claude = claude_binary()
    if claude is None:
        return
    try:
        installed = _installed(claude)
        # 查不出來(舊版 Claude Code 沒有 --json)就照舊只更新,不冒險重裝
        if installed is False:
            _install(claude, repository)
        else:
            _update(claude)
    except (OSError, subprocess.SubprocessError) as exc:
        log_warn(f"/acg 沒有更新:{exc}")
