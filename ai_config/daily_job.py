"""One command, once a day, through whatever scheduler this platform has.

autopush and keepalive each carry their own copy of this: a systemd user
timer on Linux, a LaunchAgent on macOS, a scheduled task on Windows. A
third feature needing the same thing is where it stops being copied.
"""

import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import HOME, WINDOWS_MODE
from .subproc import NATIVE, UTF8
from .systemd_timer import forget_missed_runs


def platform_name() -> str:
    if WINDOWS_MODE:
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def systemd_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(HOME / ".config")
    return Path(base) / "systemd" / "user"


@dataclass(frozen=True)
class DailyJob:
    unit: str
    label: str
    task: str
    description: str
    argv: tuple
    log_path: Path
    max_minutes: int = 15

    # ─── what gets written ────────────────────────────────────

    def systemd_units(self, hour: int, minute: int) -> "tuple[str, str]":
        command = " ".join(_quote(part) for part in self.argv)
        service = (
            "[Unit]\n"
            f"Description={self.description}\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"RuntimeMaxSec={self.max_minutes * 60}\n"
            f"ExecStart={command}\n"
        )
        timer = (
            "[Unit]\n"
            f"Description={self.description} daily\n\n"
            "[Timer]\n"
            f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\n"
            # 機器在排定時間關著或睡著時,開機後補跑
            "Persistent=true\n"
            "RandomizedDelaySec=600\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )
        return service, timer

    def launchd_plist(self, hour: int, minute: int) -> bytes:
        return plistlib.dumps({
            "Label": self.label,
            "ProgramArguments": list(self.argv),
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "RunAtLoad": False,
            "ExitTimeOut": self.max_minutes * 60,
            "StandardOutPath": str(self.log_path),
            "StandardErrorPath": str(self.log_path),
        })

    def schtasks_argv(self, hour: int, minute: int) -> list:
        """Through cmd.exe: a directly started console exe stalls under the scheduler."""
        command = " ".join(_quote(part) for part in self.argv)
        return [
            "schtasks", "/Create", "/F", "/TN", self.task, "/SC", "DAILY",
            "/ST", f"{hour:02d}:{minute:02d}", "/TR", f"cmd /c {command}",
        ]

    def launchd_path(self) -> Path:
        return HOME / "Library" / "LaunchAgents" / f"{self.label}.plist"

    # ─── install, remove, inspect ─────────────────────────────

    def enable(self, hour: int, minute: int) -> list:
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("時間要在 00:00 到 23:59 之間")
        name = platform_name()
        if name == "linux":
            return self._enable_systemd(hour, minute)
        if name == "macos":
            return self._enable_launchd(hour, minute)
        return self._enable_schtasks(hour, minute)

    def _enable_systemd(self, hour: int, minute: int) -> list:
        directory = systemd_dir()
        directory.mkdir(parents=True, exist_ok=True)
        service, timer = self.systemd_units(hour, minute)
        (directory / f"{self.unit}.service").write_text(service, encoding="utf-8")
        (directory / f"{self.unit}.timer").write_text(timer, encoding="utf-8")
        lines = [f"寫入 {directory / (self.unit + '.timer')}"]
        forget_missed_runs(f"{self.unit}.timer")
        if _systemctl("daemon-reload").returncode != 0:
            lines.append("systemctl daemon-reload 失敗,請手動執行")
            return lines
        started = _systemctl("enable", "--now", f"{self.unit}.timer")
        if started.returncode != 0:
            raise RuntimeError(f"啟用 timer 失敗:{(started.stderr or '').strip()}")
        lines.append(f"已排定每天 {hour:02d}:{minute:02d}")
        lines.append("關機錯過的排程會在開機後補跑")
        return lines

    def _enable_launchd(self, hour: int, minute: int) -> list:
        path = self.launchd_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.launchd_plist(hour, minute))
        target = f"gui/{os.getuid()}"
        # bootout 先移除舊的,否則 bootstrap 會因為已載入而失敗
        subprocess.run(
            ["launchctl", "bootout", f"{target}/{self.label}"],
            capture_output=True, check=False, timeout=30,
        )
        loaded = subprocess.run(
            ["launchctl", "bootstrap", target, str(path)],
            capture_output=True, text=True, **UTF8, check=False, timeout=30,
        )
        if loaded.returncode != 0:
            raise RuntimeError(f"載入 LaunchAgent 失敗:{(loaded.stderr or '').strip()}")
        return [f"寫入 {path}", f"已排定每天 {hour:02d}:{minute:02d}"]

    def _enable_schtasks(self, hour: int, minute: int) -> list:
        created = subprocess.run(
            self.schtasks_argv(hour, minute),
            capture_output=True, text=True, **NATIVE, check=False, timeout=60,
        )
        if created.returncode != 0:
            raise RuntimeError(
                f"建立工作排程失敗:{(created.stderr or created.stdout).strip()}"
            )
        lines = [f"已排定每天 {hour:02d}:{minute:02d}(工作排程器:{self.task})"]
        if self._limit_windows_runtime():
            lines.append(f"單次執行超過 {self.max_minutes} 分鐘會被中止")
        return lines

    def _limit_windows_runtime(self) -> bool:
        """schtasks /Create cannot set this; the default limit is 72 hours."""
        script = (
            f"$t = Get-ScheduledTask -TaskName '{self.task}'; "
            f"$t.Settings.ExecutionTimeLimit = 'PT{self.max_minutes}M'; "
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

    def disable(self) -> list:
        name = platform_name()
        if name == "linux":
            _systemctl("disable", "--now", f"{self.unit}.timer")
            removed = []
            for suffix in (".timer", ".service"):
                path = systemd_dir() / f"{self.unit}{suffix}"
                if path.exists():
                    path.unlink()
                    removed.append(path)
            _systemctl("daemon-reload")
            return [f"移除 {path}" for path in removed]
        if name == "macos":
            path = self.launchd_path()
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/{self.label}"],
                capture_output=True, check=False, timeout=30,
            )
            if not path.exists():
                return []
            path.unlink()
            return [f"移除 {path}"]
        removed = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", self.task],
            capture_output=True, text=True, **NATIVE, check=False, timeout=60,
        )
        return [f"移除工作排程:{self.task}"] if removed.returncode == 0 else []

    def installed(self) -> bool:
        name = platform_name()
        if name == "linux":
            return (systemd_dir() / f"{self.unit}.timer").is_file()
        if name == "macos":
            return self.launchd_path().is_file()
        return self._schtasks_xml() is not None

    def scheduled_at(self) -> "tuple[int, int] | None":
        """The hour and minute the scheduler holds, read back from it."""
        name = platform_name()
        try:
            if name == "linux":
                text = (systemd_dir() / f"{self.unit}.timer").read_text(encoding="utf-8")
                for line in text.splitlines():
                    if line.startswith("OnCalendar="):
                        hour, minute, _ = line.split()[-1].split(":")
                        return int(hour), int(minute)
                return None
            if name == "macos":
                when = plistlib.loads(self.launchd_path().read_bytes())["StartCalendarInterval"]
                return int(when["Hour"]), int(when["Minute"])
            xml = self._schtasks_xml()
            match = re.search(r"<StartBoundary>[^T]*T(\d{2}):(\d{2})", xml or "")
            return (int(match[1]), int(match[2])) if match else None
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def _schtasks_xml(self) -> "str | None":
        try:
            listed = subprocess.run(
                ["schtasks", "/Query", "/TN", self.task, "/XML"],
                capture_output=True, text=True, **NATIVE, check=False, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return listed.stdout if listed.returncode == 0 else None


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, **UTF8, check=False, timeout=30,
    )
