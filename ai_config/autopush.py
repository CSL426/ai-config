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

import contextlib
import io
import json
import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .console import log_info
from .memory_paths import memory_dir
from .paths import HOME, SCRIPT_DIR, WINDOWS_MODE
from .subproc import NATIVE, UTF8
from .systemd_timer import forget_missed_runs

DEFAULT_HOUR = 4
DEFAULT_STALE_HOURS = 12
_LABEL = "com.csl426.acg.autopush"
_UNIT = "acg-autopush"
_TASK = "acg memory autopush"
_MAX_MINUTES = 60


def state_path() -> Path:
    """When this machine last pushed. Local: every machine has its own.

    Keeping it in the synced notebook would let one machine's timestamp
    become another's, and the cooldown would then skip a machine that had
    not pushed at all.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "acg" / "autopush-state"


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
    failure_path().unlink(missing_ok=True)


def failure_path() -> Path:
    """Why the last unattended push did not happen, until one succeeds.

    A push blocked at 04:18 left nothing but a line in the journal; status
    kept showing the last success, and nobody learned of it for a day.
    """
    return state_path().with_name("autopush-failure.json")


def last_failure() -> "dict | None":
    try:
        record = json.loads(failure_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("reason"), str):
        return None
    paths = record.get("paths")
    return {
        "when": str(record.get("when", "")),
        "reason": record["reason"],
        "paths": [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else [],
    }


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _summarize(output: str, code: int) -> "tuple[str, list[str]]":
    """The first error line and the paths listed under it."""
    lines = [_ANSI.sub("", line) for line in output.splitlines()]
    for index, line in enumerate(lines):
        if line.startswith("✗"):
            paths = []
            for follow in lines[index + 1:]:
                if not follow.startswith("  ") or len(paths) >= 10:
                    break
                paths.append(follow.strip())
            return line[1:].strip(), paths
    return f"結束碼 {code}", []


class _Tee(io.TextIOBase):
    def __init__(self, stream, sink: io.StringIO) -> None:
        self._stream, self._sink = stream, sink

    def write(self, text: str) -> int:
        self._sink.write(text)
        return self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()


def push_and_record(push: "Callable[[], int]") -> int:
    """Run an unattended push, remembering success or why it failed.

    The output still goes where it always did (the journal, the task log);
    a copy is kept only to name the reason afterwards.
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(_Tee(sys.stdout, captured)), \
            contextlib.redirect_stderr(_Tee(sys.stderr, captured)):
        code = push()
    if code == 0:
        record_push()
        return code
    reason, paths = _summarize(captured.getvalue(), code)
    _record_failure(reason, paths)
    return code


def _record_failure(reason: str, paths: "list[str]") -> None:
    path = failure_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "when": datetime.now(UTC).isoformat(), "reason": reason, "paths": paths,
    }, ensure_ascii=False), encoding="utf-8")


def _memory_has_changes() -> "bool | None":
    """Whether the notebook differs from its last commit; None when git could not say.

    Milliseconds, against seconds for a real push. Every scheduled run
    starts here so a quiet day costs nothing.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "status", "--porcelain=v1",
             "--untracked-files=all", "--", memory_dir().name],
            capture_output=True, text=True, **UTF8, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


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
            capture_output=True, text=True, **UTF8, check=False, timeout=timeout,
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
    head = _git("rev-parse", "HEAD")
    before = head.stdout.strip() if head is not None and head.returncode == 0 else ""
    stashes = _stash_count()
    result = _git("rebase", "--autostash", "@{upstream}")
    if (result is not None and result.returncode == 0
            and not _unmerged() and _stash_count() == stashes):
        return True
    _git("rebase", "--abort")
    # 搬上去之後把本機改動套回來時衝突,git 會把改動留在 stash、把衝突標記
    # 留在工作區,資料庫就卡在半途:之後每次 pull 都失敗,一台機器因此三天
    # 沒同步。改動在 stash 裡完整保留,回到原本的 commit 再套回去,就是嘗試前的樣子
    if before and stashes is not None and (_stash_count() or 0) > stashes:
        _git("reset", "-q", "--hard", before)
        _git("stash", "pop", "-q")
    return False


def _stash_count() -> "int | None":
    listed = _git("stash", "list")
    if listed is None or listed.returncode != 0:
        return None
    return len(listed.stdout.splitlines())


def _unmerged() -> bool:
    listed = _git("diff", "--name-only", "--diff-filter=U")
    return listed is None or listed.returncode != 0 or bool(listed.stdout.strip())


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
        if wanted is None or not schedule_installed():
            return ""
        if _scheduled_at() == (wanted.hour, wanted.minute):
            return ""
        install(wanted.hour)
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
            capture_output=True, text=True, **NATIVE, check=False, timeout=60,
        )
        if listed.returncode != 0:
            return None
        match = re.search(r"<StartBoundary>[^T]*T(\d{2}):(\d{2})", listed.stdout)
        return (int(match[1]), int(match[2])) if match else None
    except (KeyError, OSError, ValueError):
        return None


def decide(stale_hours: float = DEFAULT_STALE_HOURS, preview: bool = False) -> Decision:
    """Whether a scheduled run should push, and the reason either way.

    A preview answers the same question without acting on it: it neither
    rebases onto the remote nor clears the failure record, so `status`
    stays read-only.
    """
    if not memory_dir().is_dir():
        return Decision(False, "沒有記憶目錄")
    # 先問遠端再看本機。順序反過來的話,沒有變更的機器永遠不會 fetch,
    # origin/main 會一直停在幾天前的快照,落後判斷等於失效
    # 落後就先接上,即使沒有東西要推。共用的時間表就住在記憶目錄裡,
    # 一台永遠不接上的機器會一直讀到自己那份舊的,看不到別台認領了哪一分鐘
    behind = _behind_upstream()
    caught_up = _catch_up() if behind and not preview else True
    changes = _memory_has_changes()
    if changes is None:
        # 問不到不等於沒有:失敗紀錄要留著,不然一次 git 逾時就把提醒抹掉
        return Decision(False, "無法確認記憶有沒有變更(git status 失敗)")
    if not changes:
        # 確定沒有待推的內容,之前的失敗就過去了:內容推上去了或被拿掉了
        if not preview:
            failure_path().unlink(missing_ok=True)
        return Decision(False, "記憶沒有變更")
    if not caught_up:
        reason = "落後遠端且無法自動接上,請自己 acg pull 處理"
        if not preview:
            # 只是跳過的話沒有人會知道;記成失敗,新的 session 開頭就會提醒
            _record_failure(reason, [])
        return Decision(False, reason)
    last = _read_last_push()
    if last is not None:
        waited = datetime.now(UTC) - last
        if waited < timedelta(hours=stale_hours):
            hours = waited.total_seconds() / 3600
            return Decision(False, f"{hours:.1f} 小時前才推過,未滿 {stale_hours} 小時")
    return Decision(True, "記憶有變更且距上次推送夠久")


# ─── platform scheduling ──────────────────────────────────────

def _acg_command() -> list[str]:
    from . import paths

    return paths.scheduled_command()


def _run_args(stale_hours: float) -> list[str]:
    # 每晚排程:開了自動更新就先更新,再用新版上傳(見 nightly.py)
    return [*_acg_command(), "__nightly", "--if-stale", str(stale_hours)]


def systemd_units(hour: int, stale_hours: float, minute: int = 0) -> tuple[str, str]:
    """The service and timer text. Persistent catches a machine that slept."""
    command = " ".join(_quote(part) for part in _run_args(stale_hours))
    service = (
        "[Unit]\n"
        "Description=acg nightly: update tools, save shared memory\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        # 保存記憶是幾秒的事,更新四個工具是幾分鐘。真的卡住就砍掉,
        # 不要讓一個停住的行程佔著到下一次排程,那只會讓問題更難察覺
        f"RuntimeMaxSec={_MAX_MINUTES * 60}\n"
        f"ExecStart={command}\n"
    )
    timer = (
        "[Unit]\n"
        "Description=acg nightly run\n\n"
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
        "ExitTimeOut": _MAX_MINUTES * 60,
        "StandardOutPath": str(_log_path()),
        "StandardErrorPath": str(_log_path()),
    })


def _log_path() -> Path:
    """The scheduled run's own output, on machines without a journal.

    Local state, not the notebook: a log inside the synced repository was
    committed and pushed along with the memory it was logging.
    """
    return state_path().with_name("nightly.log")


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
        "/ST", f"{hour:02d}:{minute:02d}", "/TR", windows_command(command, _log_path()),
    ]


def windows_command(command: str, log: "Path | None" = None) -> str:
    """cmd for usable standard handles, inside a console nobody sees.

    With Windows Terminal as the default console, `cmd /c` alone opened a
    visible terminal for the whole five-minute nightly run. A headless
    conhost gives the same console without a window (measured: none shown,
    result 0). --headless is undocumented; it exists since Windows 10 1809.

    With a log, the run's output replaces it each night. Linux has the
    journal; Windows kept nothing, so a failed night left only its exit
    code. Overwriting keeps it bounded: cmd holds the file open for the
    whole run, so nothing could rotate it.
    """
    if log is None:
        return f"conhost.exe --headless cmd /c {command}"
    return f"conhost.exe --headless cmd /c {command} > {_quote(str(log))} 2>&1"


def refresh_windows_task() -> str:
    """Rewrite a task made before it ran headless, at the time it already has."""
    if platform_name() != "windows" or not schedule_installed():
        return ""
    listed = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK, "/XML"],
        capture_output=True, text=True, **NATIVE, check=False, timeout=60,
    )
    at = _scheduled_at()
    current = "--headless" in listed.stdout and _log_path().name in listed.stdout
    if listed.returncode != 0 or current or at is None:
        return ""
    install(at[0])
    return f"每晚排程改成不開視窗執行,輸出寫進 {_log_path()}({at[0]:02d}:{at[1]:02d})"


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
        capture_output=True, text=True, **UTF8, check=False, timeout=30,
    )


def enable(hour: "int | None" = None) -> list[str]:
    """Turn on the nightly memory save; the schedule is shared with autoupdate."""
    from . import nightly

    return nightly.turn_on("autopush", hour)


def disable() -> list[str]:
    """Turn off the nightly save; the schedule stays while autoupdate needs it."""
    from . import nightly

    return nightly.turn_off("autopush")


def install(
    hour: "int | None" = None, stale_hours: float = DEFAULT_STALE_HOURS
) -> list[str]:
    """Register the nightly run with whatever scheduler this platform has.

    With no hour given, take one from the shared table so machines do not
    all wake at once. An explicit hour wins and is recorded, so asking for
    a time is also how you change your own slot.
    """
    if stale_hours < 0:
        raise ValueError("冷卻時數不能是負的")
    # 先驗證再寫表:寫了才拒絕會把這台的表項變成 -1:00 之類讀不回來的值,
    # 下次就當成沒認領過、從頭搶時段
    if hour is not None and not 0 <= hour <= 23:
        raise ValueError("時間要在 0 到 23 之間")
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
            # 已經有時段但和別台撞在一起時,這裡就讓位,不必等到排程執行
            moved = table.resolve_collision(current, host)
            if moved is not None:
                slot = moved
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
    forget_missed_runs(f"{_UNIT}.timer")
    reloaded = _systemctl("daemon-reload")
    started = reloaded if reloaded.returncode != 0 else _systemctl(
        "enable", "--now", f"{_UNIT}.timer"
    )
    if started.returncode != 0:
        # 留著檔案的話狀態會說已排定,其實 systemd 根本沒載入
        for suffix in (".timer", ".service"):
            (directory / f"{_UNIT}{suffix}").unlink(missing_ok=True)
        raise RuntimeError(f"啟用 timer 失敗:{(started.stderr or '').strip()}")
    lines.append(f"已排定每天 {hour:02d}:{minute:02d}")
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
        capture_output=True, text=True, **UTF8, check=False, timeout=30,
    )
    if loaded.returncode != 0:
        lines.append(f"載入 LaunchAgent 失敗:{(loaded.stderr or '').strip()}")
        return lines
    lines.append(f"已排定每天 {hour:02d}:{minute:02d}")
    return lines


def _enable_schtasks(hour: int, stale_hours: float, minute: int = 0) -> list[str]:
    # cmd 開不了日誌檔就整個不跑,目錄要先在
    _log_path().parent.mkdir(parents=True, exist_ok=True)
    created = subprocess.run(
        schtasks_argv(hour, stale_hours, minute),
        capture_output=True, text=True, **NATIVE, check=False, timeout=60,
    )
    if created.returncode != 0:
        raise RuntimeError(
            f"建立工作排程失敗:{(created.stderr or created.stdout).strip()}"
        )
    lines = [f"已排定每天 {hour:02d}:{minute:02d}(工作排程器:{_TASK})"]
    if _limit_windows_runtime():
        lines.append(f"單次執行超過 {_MAX_MINUTES} 分鐘會被中止")
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
        f"$t.Settings.ExecutionTimeLimit = 'PT{_MAX_MINUTES}M'; "
        "Set-ScheduledTask -TaskName $t.TaskName -Settings $t.Settings"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, **NATIVE, check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def uninstall() -> list[str]:
    """Remove the schedule. Leaves the memory and its state file alone."""
    name = platform_name()
    if name == "linux":
        timer = systemd_dir() / f"{_UNIT}.timer"
        stopped = _systemctl("disable", "--now", f"{_UNIT}.timer")
        if stopped.returncode != 0 and timer.exists():
            # 檔案刪了但 timer 還在跑的話,狀態會說已停用,排程卻照舊
            raise RuntimeError(f"停用 timer 失敗:{(stopped.stderr or '').strip()}")
        removed = []
        for suffix in (".timer", ".service"):
            path = systemd_dir() / f"{_UNIT}{suffix}"
            if path.exists():
                path.unlink()
                removed.append(str(path))
        _systemctl("daemon-reload")
        return [f"移除 {path}" for path in removed] or ["沒有排定的每晚排程"]
    if name == "macos":
        path = launchd_path()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{_LABEL}"],
            capture_output=True, check=False, timeout=30,
        )
        if not path.exists():
            return ["沒有排定的每晚排程"]
        path.unlink()
        return [f"移除 {path}"]
    removed = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", _TASK],
        capture_output=True, text=True, **NATIVE, check=False, timeout=60,
    )
    if removed.returncode != 0:
        return ["沒有排定的每晚排程"]
    return [f"移除工作排程:{_TASK}"]


def status() -> dict:
    """What is scheduled, when it last pushed, and what it would do now."""
    from . import nightly

    last = _read_last_push()
    decision = decide(preview=True)
    return {
        "platform": platform_name(),
        "installed": nightly.enabled("autopush"),
        # 自動上傳關了、只剩自動更新時排程也還在,時段設定要看這個
        "scheduled": schedule_installed(),
        "last_push": last.isoformat() if last else "",
        "last_failure": last_failure(),
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
        from . import nightly

        if nightly.enabled("autopush"):
            return
        decision = decide()
        if not decision.push:
            return
        from .commands.push import do_push
        from .console import set_force
        from .push_preflight import MEMORY_SCOPE

        # 同上:只跳過確認,憑證檢查仍然會擋下不該外流的內容
        set_force(True)
        log_info(
            f"記憶超過 {DEFAULT_STALE_HOURS:g} 小時沒保存,正在自動上傳…"
        )
        push_and_record(lambda: do_push(MEMORY_SCOPE, allow_secrets=False))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        # 順手做的事不該讓使用者原本的指令失敗
        return


def schedule_installed() -> bool:
    name = platform_name()
    if name == "linux":
        return (systemd_dir() / f"{_UNIT}.timer").is_file()
    if name == "macos":
        return launchd_path().is_file()
    listed = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK],
        capture_output=True, text=True, **NATIVE, check=False, timeout=30,
    )
    return listed.returncode == 0
