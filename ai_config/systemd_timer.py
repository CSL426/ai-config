"""Rescheduling a systemd user timer without an unwanted catch-up run."""

import os
import subprocess
from pathlib import Path

from .paths import HOME
from .subproc import UTF8


def stamp_path(timer: str) -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(HOME / ".local" / "share")
    return Path(base) / "systemd" / "timers" / f"stamp-{timer}"


def forget_missed_runs(timer: str) -> None:
    """Call before daemon-reload when a timer's times change.

    systemd computes a calendar timer's next run from its last trigger.
    A running timer keeps that across daemon-reload, so a new time that
    falls between the last run and now fires at once: moving 17:10 to
    17:00/21:00 at 21:00 woke the tool a second time. Only starting the
    timer re-reads the last trigger, and it reads it from the stamp file.
    """
    try:
        subprocess.run(
            ["systemctl", "--user", "stop", timer],
            capture_output=True, text=True, **UTF8, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return
    stamp = stamp_path(timer)
    try:
        if stamp.is_file():
            os.utime(stamp)
    except OSError:
        # 碰不到 stamp 頂多多喚醒一次,不該讓排程裝不起來
        pass
