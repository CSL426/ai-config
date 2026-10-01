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
    # 沒有失敗,但安裝方式讓它沒辦法自動更新,要有人處理;不讓排程算失敗
    warn: bool = False

    def line(self) -> str:
        mark = "✗" if not self.ok else "⚠" if self.warn else "✓"
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


def _run(argv: list, timeout: float = _STEP_TIMEOUT, extra_env: "dict | None" = None) -> "tuple[int, str]":
    env = {
        **os.environ, "AI_CONFIG_NO_UPDATE_CHECK": "1", "AI_CONFIG_NO_AUTOPUSH": "1",
        **(extra_env or {}),
    }
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


def _update(name: str, argv: list, command: list, extra_env: "dict | None" = None) -> Step:
    step = Step(name, before=_version(argv))
    code, output = _run([*argv, *command], extra_env=extra_env)
    step.after = _version(argv)
    if code != 0:
        step.ok, step.note = False, _reason(code, output)
    return step


def _codex_home(binary: str) -> "Path | None":
    """The CODEX_HOME a standalone codex was installed into.

    `codex update` finds its own install under $CODEX_HOME/packages/standalone;
    one installed into ~/.codex-work answers "Could not detect the Codex
    installation method" when the scheduler runs it with the default home.
    """
    try:
        resolved = Path(binary).resolve()
    except OSError:
        return None
    for parent in resolved.parents:
        if parent.name == "standalone" and parent.parent.name == "packages":
            return parent.parent.parent
    return None


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


_ASIDE = ".acg-old"


def _running_executables() -> "set[Path] | None":
    """Where this user's processes run from; None when that cannot be told.

    Windows answers through the rename in _remove_release instead: a
    directory holding a running executable cannot be renamed there.
    """
    if os.name == "nt":
        return set()
    proc = Path("/proc")
    if proc.is_dir():
        found = set()
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                target = os.readlink(entry / "exe")
            except OSError:
                continue
            found.add(Path(target.removesuffix(" (deleted)")))
        return found
    try:
        listed = subprocess.run(
            ["ps", "-axo", "comm="], capture_output=True, text=True, **UTF8,
            check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    return {Path(line.strip()) for line in listed.stdout.splitlines() if line.strip()}


def _tree_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


def _remove_release(release: Path) -> bool:
    """Rename aside, then delete; False when something still runs from it."""
    aside = release.with_name(f"{release.name}{_ASIDE}")
    try:
        release.rename(aside)
    except OSError:
        return False
    shutil.rmtree(aside, ignore_errors=True)
    return True


def _prune_codex_releases(home: "Path | None" = None) -> "tuple[int, int]":
    """Delete the codex releases nothing points at any more: (bytes freed, releases kept).

    The standalone installer and the app-server daemon each unpack every
    new version into <CODEX_HOME>/packages/<kind>/releases/<version> and
    switch `current` to it, never removing the old ones: one machine
    carried 14 GB of them across three homes. The release `current` names
    stays, and so does one a running codex still executes from.
    """
    from .safety import is_reparse_point

    home = home or HOME
    running = _running_executables()
    if running is None:
        return 0, 0
    freed = kept = 0
    for releases in sorted(home.glob(".codex*/packages/*/releases")):
        if is_reparse_point(releases) or not releases.is_dir():
            continue
        try:
            current = (releases.parent / "current").resolve(strict=True)
            entries = sorted(releases.iterdir())
        except OSError:
            continue  # 不知道哪一版在用,整個目錄都不碰
        for release in entries:
            if release.name.endswith(_ASIDE):
                # 上次搬開了卻沒刪完的,這次接著刪
                shutil.rmtree(release, ignore_errors=True)
                continue
            if is_reparse_point(release) or not release.is_dir():
                continue
            real = release.resolve()
            if real == current:
                continue
            if any(path.is_relative_to(real) for path in running):
                kept += 1
                continue
            size = _tree_size(release)
            if _remove_release(release):
                freed += size
            else:
                kept += 1
    return freed, kept


def _update_tool(tool: str) -> "Step | None":
    binary = _binary(tool)
    if binary is None:
        return None
    if tool == "codex" and _is_npm_install(binary):
        # npm 全域安裝多半在要 sudo 的系統目錄;自動跑只會失敗,改成提醒
        step = Step(tool, before=_version([binary]), warn=True, note=(
            f"npm 全域安裝({binary}),不自動更新;"
            "建議改用官方獨立安裝版"
        ))
    else:
        home = _codex_home(binary) if tool == "codex" else None
        step = _update(tool, [binary], ["update"], {"CODEX_HOME": str(home)} if home else None)
        step.freed, step.kept = _remove_replaced(binary)
    if tool == "codex":
        # npm 版也會有 daemon 自己下載的版本堆在 packages 底下
        freed, kept = _prune_codex_releases()
        step.freed += freed
        step.kept += kept
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
            warn=raw.get("warn") is True,
        ))
    return {"when": str(record.get("when", "")), "steps": steps}


def failures() -> "tuple[str, list[Step]]":
    """When the last run was and what needs a person: failed or warned steps.

    A run where everything succeeds without a warning clears it.
    """
    last = last_run()
    if last is None:
        return "", []
    return last["when"], [step for step in last["steps"] if not step.ok or step.warn]


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
