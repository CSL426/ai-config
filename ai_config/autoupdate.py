"""Bring acg and every AI CLI on this machine up to date, once a day.

Updates that wait for a person drift: on 2026-09-30 two machines each
still carried a stale Codex (0.147.0, 0.77.0) nobody had noticed, and
three machines once sat a dozen acg releases apart. Each CLI already knows
how to update itself; this only makes sure somebody asks it to.

Every tool here installs a new version beside the old one and swaps a
link or renames, so a session that is running during the update keeps
the files it started with.
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import paths
from .paths import HOME
from .subproc import UTF8

TOOLS = ("claude", "codex", "agy")
_STEP_TIMEOUT = 600
_VERSION = re.compile(r"\d+(?:\.\d+)+")


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "acg"


def result_path() -> Path:
    return state_dir() / "autoupdate-last.json"


@dataclass
class Step:
    name: str
    before: str = ""
    after: str = ""
    ok: bool = True
    note: str = ""
    freed: int = 0
    kept: int = 0

    def line(self) -> str:
        mark = "✓" if self.ok else "✗"
        if self.note:
            text = self.note
        elif self.before and self.after and self.before != self.after:
            text = f"{self.before} → {self.after}"
        else:
            text = f"{self.after or self.before} 已是最新"
        if self.freed:
            text += f"(清掉舊執行檔 {self.freed // 2**20} MB)"
        if self.kept:
            # 只說清掉多少,會讓人以為殘留都沒了;被佔用刪不掉的也要講
            text += f"({self.kept} 個舊執行檔使用中,下次再清)"
        return f"{mark} {self.name}:{text}"


# ─── finding and asking each tool ─────────────────────────────

def _binary(tool: str) -> "str | None":
    """The stable launcher, found even where the scheduler's PATH is bare.

    A systemd user service starts without ~/.local/bin on PATH, which is
    exactly where these CLIs install themselves.
    """
    from .keepalive_settings import tool_binary

    found = tool_binary(tool)
    if Path(found).is_file():
        return found
    return shutil.which(found)


def _run(argv: list, timeout: float = _STEP_TIMEOUT) -> "tuple[int, str]":
    env = {**os.environ, "AI_CONFIG_NO_UPDATE_CHECK": "1", "AI_CONFIG_NO_AUTOPUSH": "1"}
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, **UTF8, check=False,
            timeout=timeout, stdin=subprocess.DEVNULL, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return 124, f"{int(timeout)} 秒內沒有結束"
    except OSError as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _version(argv: list) -> str:
    code, output = _run([*argv, "--version"], timeout=60)
    match = _VERSION.search(output) if code == 0 else None
    return match.group(0) if match else ""


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ERROR_HINT = re.compile(r"error|fail|denied|permission|invalid|✗|失敗", re.IGNORECASE)


def _reason(code: int, output: str) -> str:
    """The line that says why, rather than the last line printed."""
    lines = [_ANSI.sub("", line).strip() for line in output.splitlines()]
    lines = [line for line in lines if line]
    flagged = [line for line in lines if _ERROR_HINT.search(line)]
    picked = (flagged or lines or [f"結束碼 {code}"])[-1]
    return picked[:300]


def _is_npm_install(binary: str) -> bool:
    """Linux links into node_modules; Windows puts a codex.cmd shim in %APPDATA%\\npm."""
    path = Path(binary)
    if path.parent.name.lower() == "npm":
        return True
    try:
        return "node_modules" in path.resolve().parts
    except OSError:
        return False


def _update(name: str, argv: list, command: list) -> Step:
    step = Step(name, before=_version(argv))
    code, output = _run([*argv, *command])
    step.after = _version(argv)
    if code != 0:
        step.ok, step.note = False, _reason(code, output)
    return step


def _remove_replaced(binary: str) -> "tuple[int, int]":
    """Delete the executables a self-update renamed aside: (bytes freed, files kept).

    agy leaves agy.<n>.old and claude on Windows claude.exe.old.<n> beside
    the launcher, 200 MB each, and never removes them: one Windows machine
    carried 700 MB of them. A daily update would add one a day. A copy a
    running session still holds cannot be deleted on Windows; it stays for
    the next run. On a machine with a dozen Claude sessions open that can
    be every run, so the kept count is reported rather than hidden.
    """
    launcher = Path(binary)
    leftovers = {
        leftover
        for pattern in (f"{launcher.name}.*.old", f"{launcher.name}.old.*")
        for leftover in launcher.parent.glob(pattern)
    }
    freed = kept = 0
    for leftover in sorted(leftovers):
        try:
            if leftover.is_symlink() or not leftover.is_file():
                continue
            size = leftover.stat().st_size
        except OSError:
            continue
        try:
            leftover.unlink()
        except OSError:
            # 只有刪不掉才算「使用中」;讀不到大小的不是這種情況
            kept += 1
            continue
        freed += size
    return freed, kept


def _update_tool(tool: str) -> "Step | None":
    binary = _binary(tool)
    if binary is None:
        return None
    if tool == "codex" and _is_npm_install(binary):
        # npm 全域安裝多半在要 sudo 的系統目錄;自動跑只會失敗,改成提醒
        return Step(tool, before=_version([binary]), note=(
            f"npm 全域安裝({binary}),不自動更新;"
            "建議改用官方獨立安裝版"
        ))
    step = _update(tool, [binary], ["update"])
    step.freed, step.kept = _remove_replaced(binary)
    return step


def _update_acg() -> Step:
    return _update("acg", paths.scheduled_command(), ["update"])


# ─── one run ──────────────────────────────────────────────────

def run() -> int:
    """Update every tool, one failing not stopping the rest, and remember how it went.

    acg goes last: its update also refreshes the /acg plugin through
    `claude`, which should already be the new one by then.
    """
    from .locking import exclusive_lock

    # 排程補跑與手動 run 撞在一起時,兩邊會同時替換同一支執行檔
    with exclusive_lock(".acg-autoupdate.lock") as acquired:
        if not acquired:
            print("✗ 已經有一次自動更新正在進行,這次不做", flush=True)
            return 1
        return _run_all()


def _run_all() -> int:
    steps = [step for step in (_update_tool(tool) for tool in TOOLS) if step]
    steps.append(_update_acg())
    for step in steps:
        print(step.line(), flush=True)
    record = {"when": datetime.now(UTC).isoformat(), "steps": [asdict(s) for s in steps]}
    path = result_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return 0 if all(step.ok for step in steps) else 1


def last_run() -> "dict | None":
    try:
        record = json.loads(result_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("steps"), list):
        return None
    steps = []
    for raw in record["steps"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            continue
        steps.append(Step(
            name=raw["name"], before=str(raw.get("before", "")),
            after=str(raw.get("after", "")), ok=raw.get("ok") is not False,
            note=str(raw.get("note", "")),
            freed=raw["freed"] if isinstance(raw.get("freed"), int) else 0,
            kept=raw["kept"] if isinstance(raw.get("kept"), int) else 0,
        ))
    return {"when": str(record.get("when", "")), "steps": steps}


def failures() -> "tuple[str, list[Step]]":
    """When the last run was and what failed in it; nothing once a run succeeds."""
    last = last_run()
    if last is None:
        return "", []
    return last["when"], [step for step in last["steps"] if not step.ok]


# ─── schedule ─────────────────────────────────────────────────
# 沒有自己的排程:跟自動上傳共用每晚那一個,先更新再上傳(見 nightly.py)

def enable(hour: "int | None" = None) -> list:
    from . import nightly

    return nightly.turn_on("autoupdate", hour)


def disable() -> list:
    from . import nightly

    return nightly.turn_off("autoupdate")


def status() -> dict:
    from . import autopush, nightly

    at = autopush._scheduled_at() if autopush.schedule_installed() else None
    last = last_run()
    return {
        "installed": nightly.enabled("autoupdate"),
        "time": f"{at[0]:02d}:{at[1]:02d}" if at else "",
        "last_run": last["when"] if last else "",
        "steps": [asdict(step) for step in last["steps"]] if last else [],
    }
