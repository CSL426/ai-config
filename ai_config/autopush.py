"""Save the shared memory once a day, without anyone being at the keyboard.

Memory accumulates while you work and is worth nothing until it leaves the
machine. Pushing it is a chore nobody remembers, so this schedules it: the
platform's own scheduler wakes acg at a quiet hour, and acg decides whether
there is anything to send.

The decision is deliberately cheap. Asking git whether the notebook changed
costs milliseconds; a full push costs seconds, so a run with nothing to do
stops before touching anything. A recent push is skipped too, which keeps a
day from filling up with one commit per session.
"""

import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .console import log_info
from .memory import memory_dir
from .paths import HOME, SCRIPT_DIR, WINDOWS_MODE

DEFAULT_HOUR = 4
DEFAULT_STALE_HOURS = 12
_LABEL = "com.csl426.acg.autopush"
_UNIT = "acg-autopush"
_TASK = "acg memory autopush"


def state_path() -> Path:
    return memory_dir() / ".autopush-state"


def _read_last_push() -> "datetime | None":
    try:
        text = state_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def record_push(when: "datetime | None" = None) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((when or datetime.now(UTC)).isoformat(), encoding="utf-8")


def _memory_has_changes() -> bool:
    """Whether the notebook differs from its last commit.

    Milliseconds, against seconds for a real push. Every scheduled run
    starts here so a quiet day costs nothing.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "status", "--porcelain=v1",
             "--untracked-files=all", "--", memory_dir().name],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _provider() -> str:
    try:
        from .config import configured_remote_provider

        return configured_remote_provider()
    except (ImportError, OSError, RuntimeError, ValueError):
        return "git"


def _git(*args: str, timeout: float = 120) -> "subprocess.CompletedProcess | None":
    try:
        return subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), *args],
            capture_output=True, text=True, check=False, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _behind_upstream() -> bool:
    """Whether the remote moved ahead of us, asking the remote itself.

    Without a fetch this compares against whatever origin/main was cached,
    which on a machine that has not pulled for days says "level" while the
    remote has moved on. The scheduled push would then hit a rejection it
    could have predicted.

    Only git has this problem. A Drive repository has no upstream ref and
    its push overwrites, so there is nothing to be behind of.
    """
    if _provider() != "git":
        return False
    _git("fetch", "--quiet")
    result = _git("rev-list", "--count", "HEAD..@{upstream}", timeout=30)
    if result is None or result.returncode != 0:
        return False
    return result.stdout.strip() not in ("", "0")


def _catch_up() -> bool:
    """Replay our memory commits on top of the remote. False if it conflicts.

    Several machines pushing on the same schedule will each be behind by
    the time they wake. Rebasing keeps them from rejecting each other; a
    real conflict aborts and stays for a person, because resolving one
    unattended could silently drop somebody's notes.
    """
    result = _git("rebase", "--autostash", "@{upstream}")
    if result is not None and result.returncode == 0:
        return True
    _git("rebase", "--abort")
    return False


@dataclass
class Decision:
    push: bool
    reason: str


def reconcile_slot() -> str:
    """Move this machine's schedule if the shared table gave it a new slot.

    The table is edited on one machine and reaches the others through the
    notebook, so the run that reads it is the one that must act on it.
    Doing it here means a changed slot takes effect by itself, at most a
    day later, without anyone re-running enable on every machine.
    """
    try:
        from . import schedule_table as table

        host = table.host_name()
        current = table.load()
        # 兩台同時 enable 會挑到同一分鐘,誰也看不到誰;同步之後才看得見,
        # 所以讓位要在這裡做
        moved = table.resolve_collision(current, host)
        if moved is not None:
            table.record(host, moved)
            wanted = moved
        else:
            wanted = current.hosts.get(host)
        if wanted is None or not _schedule_installed():
            return ""
        if _scheduled_at() == (wanted.hour, wanted.minute):
            return ""
        enable(wanted.hour)
        return f"時段改為 {wanted},排程已跟著更新"
    except (ImportError, OSError, RuntimeError, ValueError):
        return ""


def _scheduled_at() -> "tuple[int, int] | None":
    """The hour and minute this machine's scheduler currently holds."""
    name = platform_name()
    try:
        if name == "linux":
            text = (systemd_dir() / f"{_UNIT}.timer").read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("OnCalendar="):
                    clock = line.split()[-1]
                    hour, minute, _ = clock.split(":")
                    return int(hour), int(minute)
            return None
        if name == "macos":
            import plistlib

            parsed = plistlib.loads(launchd_path().read_bytes())
            when = parsed.get("StartCalendarInterval") or {}
            return int(when["Hour"]), int(when["Minute"])
        listed = subprocess.run(
            ["schtasks", "/Query", "/TN", _TASK, "/XML"],
            capture_output=True, text=True, check=False, timeout=60,
        )
        if listed.returncode != 0:
            return None
        match = re.search(r"<StartBoundary>[^T]*T(\d{2}):(\d{2})", listed.stdout)
        return (int(match[1]), int(match[2])) if match else None
    except (KeyError, OSError, ValueError):
        return None


def decide(stale_hours: float = DEFAULT_STALE_HOURS) -> Decision:
    """Whether a scheduled run should push, and the reason either way."""
    if not memory_dir().is_dir():
        return Decision(False, "沒有記憶目錄")
    # 先問遠端再看本機。順序反過來的話,沒有變更的機器永遠不會 fetch,
    # origin/main 會一直停在幾天前的快照,落後判斷等於失效
    # 落後就先接上,即使沒有東西要推。共用的時間表就住在記憶目錄裡,
    # 一台永遠不接上的機器會一直讀到自己那份舊的,看不到別台認領了哪一分鐘
    behind = _behind_upstream()
    caught_up = _catch_up() if behind else True
    if not _memory_has_changes():
        return Decision(False, "記憶沒有變更")
    if not caught_up:
        return Decision(False, "落後遠端且無法自動接上,請自己 acg pull 處理")
    last = _read_last_push()
    if last is not None:
        waited = datetime.now(UTC) - last
        if waited < timedelta(hours=stale_hours):
            hours = waited.total_seconds() / 3600
            return Decision(False, f"{hours:.1f} 小時前才推過,未滿 {stale_hours} 小時")
    return Decision(True, "記憶有變更且距上次推送夠久")


# ─── platform scheduling ──────────────────────────────────────

def _acg_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "ai_config"]


def _run_args(stale_hours: float) -> list[str]:
    return [*_acg_command(), "memory", "push", "--if-stale", str(stale_hours)]


def systemd_units(hour: int, stale_hours: float, minute: int = 0) -> tuple[str, str]:
    """The service and timer text. Persistent catches a machine that slept."""
    command = " ".join(_quote(part) for part in _run_args(stale_hours))
    service = (
        "[Unit]\n"
        "Description=Save acg shared memory\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        # 保存記憶是幾秒的事。真的卡住就砍掉,不要讓一個停住的行程
        # 佔著到下一次排程,那只會讓問題更難察覺
        "RuntimeMaxSec=900\n"
        f"ExecStart={command}\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Save acg shared memory daily\n\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\n"
        # 機器在排定時間關著或睡著時,開機後補跑,不要整天漏掉
        "Persistent=true\n"
        "RandomizedDelaySec=600\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return service, timer


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def launchd_plist(hour: int, stale_hours: float, minute: int = 0) -> bytes:
    """A LaunchAgent. RunAtLoad covers a Mac asleep at the scheduled hour."""
    return plistlib.dumps({
        "Label": _LABEL,
        "ProgramArguments": _run_args(stale_hours),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "ExitTimeOut": 900,
        "StandardOutPath": str(_log_path()),
        "StandardErrorPath": str(_log_path()),
    })


def _log_path() -> Path:
    return memory_dir() / "logs" / "autopush.log"


def schtasks_argv(hour: int, stale_hours: float, minute: int = 0) -> list[str]:
    """Windows: /F replaces an existing task so enable stays idempotent.

    Run through cmd.exe rather than starting the exe directly. The task
    scheduler hands a directly-started process standard handles a console
    program cannot use, and the run then stalls before it does any work;
    going through cmd gives it usable ones. Measured on Windows: direct
    start hangs, the same command behind cmd finishes in 1.5 seconds.
    """
    command = " ".join(_quote(part) for part in _run_args(stale_hours))
    return [
        "schtasks", "/Create", "/F", "/TN", _TASK, "/SC", "DAILY",
        "/ST", f"{hour:02d}:{minute:02d}", "/TR", f"cmd /c {command}",
    ]


def systemd_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(HOME / ".config")
    return Path(base) / "systemd" / "user"


def launchd_path() -> Path:
    return HOME / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def platform_name() -> str:
    if WINDOWS_MODE:
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# ─── install and remove ───────────────────────────────────────

def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, check=False, timeout=30,
    )


def enable(
    hour: "int | None" = None, stale_hours: float = DEFAULT_STALE_HOURS
) -> list[str]:
    """Register the daily run with whatever scheduler this platform has.

    With no hour given, take one from the shared table so machines do not
    all wake at once. An explicit hour wins and is recorded, so asking for
    a time is also how you change your own slot.
    """
    if stale_hours < 0:
        raise ValueError("冷卻時數不能是負的")
    lines, minute = [], 0
    slot = _claim_slot(hour)
    if slot is not None:
        hour, minute = slot.hour, slot.minute
        lines.append(f"這台在共用時間表裡的時段是 {slot}")
    elif hour is None:
        hour = DEFAULT_HOUR
    if not 0 <= hour <= 23:
        raise ValueError("時間要在 0 到 23 之間")
    name = platform_name()
    if name == "linux":
        lines.extend(_enable_systemd(hour, stale_hours, minute))
    elif name == "macos":
        lines.extend(_enable_launchd(hour, stale_hours, minute))
    else:
        lines.extend(_enable_schtasks(hour, stale_hours, minute))
    return lines


def _claim_slot(hour: "int | None"):
    """Pick this machine's minute and write it back for the others to see.

    An hour given by hand keeps whatever minute the table already holds,
    so setting a time in one place does not silently move it in another.
    """
    try:
        from . import schedule_table as table

        host = table.host_name()
        current = table.load()
        if hour is None:
            slot = table.claim(current, host)
        else:
            held = current.hosts.get(host)
            slot = table.Slot(hour, held.minute if held else 0)
        table.record(host, slot)
        return slot
    except (ImportError, OSError, RuntimeError, ValueError):
        # 時間表只是協調用的;讀不到就照預設走,不要讓排程裝不起來
        return None


def _enable_systemd(hour: int, stale_hours: float, minute: int = 0) -> list[str]:
    directory = systemd_dir()
    directory.mkdir(parents=True, exist_ok=True)
    service, timer = systemd_units(hour, stale_hours, minute)
    (directory / f"{_UNIT}.service").write_text(service, encoding="utf-8")
    (directory / f"{_UNIT}.timer").write_text(timer, encoding="utf-8")
    lines = [f"寫入 {directory / (_UNIT + '.timer')}"]
    reload_result = _systemctl("daemon-reload")
    if reload_result.returncode != 0:
        lines.append("systemctl daemon-reload 失敗,請手動執行")
        return lines
    started = _systemctl("enable", "--now", f"{_UNIT}.timer")
    if started.returncode != 0:
        lines.append(f"啟用 timer 失敗:{(started.stderr or '').strip()}")
        return lines
    lines.append(f"已排定每天 {hour:02d}:00 檢查")
    lines.append("關機錯過的排程會在開機後補跑")
    return lines


def _enable_launchd(hour: int, stale_hours: float, minute: int = 0) -> list[str]:
    path = launchd_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launchd_plist(hour, stale_hours, minute))
    lines = [f"寫入 {path}"]
    # bootout 先移除舊的,否則 bootstrap 會因為已載入而失敗
    target = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{target}/{_LABEL}"],
        capture_output=True, check=False, timeout=30,
    )
    loaded = subprocess.run(
        ["launchctl", "bootstrap", target, str(path)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if loaded.returncode != 0:
        lines.append(f"載入 LaunchAgent 失敗:{(loaded.stderr or '').strip()}")
        return lines
    lines.append(f"已排定每天 {hour:02d}:00 檢查")
    return lines


def _enable_schtasks(hour: int, stale_hours: float, minute: int = 0) -> list[str]:
    created = subprocess.run(
        schtasks_argv(hour, stale_hours, minute),
        capture_output=True, text=True, check=False, timeout=60,
    )
    if created.returncode != 0:
        raise RuntimeError(
            f"建立工作排程失敗:{(created.stderr or created.stdout).strip()}"
        )
    lines = [f"已排定每天 {hour:02d}:00 檢查(工作排程器:{_TASK})"]
    if _limit_windows_runtime():
        lines.append("單次執行超過 15 分鐘會被中止")
    return lines


def _limit_windows_runtime() -> bool:
    """Cap how long one run may take. schtasks /Create cannot express this.

    The scheduler's default is 72 hours, so anything that does stall sits
    there until the day after tomorrow. Saving memory takes seconds; a run
    still going after fifteen minutes is stuck, and killing it is kinder
    than hiding it.
    """
    script = (
        f"$t = Get-ScheduledTask -TaskName '{_TASK}'; "
        "$t.Settings.ExecutionTimeLimit = 'PT15M'; "
        "Set-ScheduledTask -TaskName $t.TaskName -Settings $t.Settings"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def disable() -> list[str]:
    """Remove the schedule. Leaves the memory and its state file alone."""
    name = platform_name()
    if name == "linux":
        _systemctl("disable", "--now", f"{_UNIT}.timer")
        removed = []
        for suffix in (".timer", ".service"):
            path = systemd_dir() / f"{_UNIT}{suffix}"
            if path.exists():
                path.unlink()
                removed.append(str(path))
        _systemctl("daemon-reload")
        return [f"移除 {path}" for path in removed] or ["沒有排定的自動推送"]
    if name == "macos":
        path = launchd_path()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{_LABEL}"],
            capture_output=True, check=False, timeout=30,
        )
        if not path.exists():
            return ["沒有排定的自動推送"]
        path.unlink()
        return [f"移除 {path}"]
    removed = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", _TASK],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if removed.returncode != 0:
        return ["沒有排定的自動推送"]
    return [f"移除工作排程:{_TASK}"]


def status() -> dict:
    """What is scheduled, when it last pushed, and what it would do now."""
    name = platform_name()
    if name == "linux":
        installed = (systemd_dir() / f"{_UNIT}.timer").is_file()
    elif name == "macos":
        installed = launchd_path().is_file()
    else:
        listed = subprocess.run(
            ["schtasks", "/Query", "/TN", _TASK],
            capture_output=True, text=True, check=False, timeout=60,
        )
        installed = listed.returncode == 0
    last = _read_last_push()
    decision = decide()
    return {
        "platform": name,
        "installed": installed,
        "last_push": last.isoformat() if last else "",
        "would_push": decision.push,
        "reason": decision.reason,
    }


def opportunistic_push() -> None:
    """Push on the way out of an ordinary command, when nothing schedules it.

    A user who declined the scheduler still wants the notebook saved. This
    rides on commands they run anyway, so it must stay silent and cheap:
    it does nothing at all once a schedule exists, and the decision below
    costs milliseconds on a quiet day.
    """
    if os.environ.get("AI_CONFIG_NO_AUTOPUSH"):
        return
    try:
        if _schedule_installed():
            return
        decision = decide()
        if not decision.push:
            return
        from .commands.push import MEMORY_SCOPE, do_push
        from .console import set_force

        # 同上:只跳過確認,憑證檢查仍然會擋下不該外流的內容
        set_force(True)
        log_info(
            f"記憶超過 {DEFAULT_STALE_HOURS:g} 小時沒保存,正在自動上傳…"
        )
        if do_push(MEMORY_SCOPE, allow_secrets=False) == 0:
            record_push()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        # 順手做的事不該讓使用者原本的指令失敗
        return


def _schedule_installed() -> bool:
    name = platform_name()
    if name == "linux":
        return (systemd_dir() / f"{_UNIT}.timer").is_file()
    if name == "macos":
        return launchd_path().is_file()
    listed = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK],
        capture_output=True, text=True, check=False, timeout=30,
    )
    return listed.returncode == 0
