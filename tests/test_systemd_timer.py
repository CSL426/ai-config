"""Changing a timer's times must not wake the tool for a slot already past."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from ai_config import autopush, keepalive, systemd_timer


@pytest.fixture
def systemctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list:
    """Record systemctl calls, and the stamp's age at each one."""
    calls = []
    stamp = tmp_path / "watched-stamp"

    def run(args, **_kwargs):
        if args[:2] == ["systemctl", "--user"]:
            age = time.time() - stamp.stat().st_mtime if stamp.exists() else None
            calls.append((args[2], age))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(keepalive, "_systemd_dir", lambda: tmp_path / "units")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        systemd_timer, "stamp_path", lambda timer: stamp if timer in WATCHED else
        tmp_path / "other" / timer,
    )
    return calls


WATCHED = {"acg-keepalive.timer", "acg-autopush.timer"}


def _old_stamp(timer: str) -> Path:
    stamp = systemd_timer.stamp_path(timer)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    hours_ago = time.time() - 4 * 3600
    os.utime(stamp, (hours_ago, hours_ago))
    return stamp


def test_keepalive_stops_and_restamps_before_reloading(systemctl: list) -> None:
    _old_stamp("acg-keepalive.timer")

    keepalive._enable_systemd(["07:00", "12:00", "17:00", "22:00"], "claude")

    actions = [action for action, _ in systemctl]
    assert actions[:3] == ["stop", "daemon-reload", "enable"]
    # reload 之前 stamp 就得是「剛剛」,不然 systemd 會補跑中間的時段
    reload_age = systemctl[1][1]
    assert reload_age is not None and reload_age < 60


def test_autopush_stops_and_restamps_before_reloading(systemctl: list) -> None:
    _old_stamp("acg-autopush.timer")

    autopush._enable_systemd(4, 12.0)

    actions = [action for action, _ in systemctl]
    assert actions[:3] == ["stop", "daemon-reload", "enable"]
    assert systemctl[1][1] is not None and systemctl[1][1] < 60


def test_a_timer_that_never_ran_gets_no_stamp(systemctl: list) -> None:
    # 沒有 stamp 時 systemd 從啟動時間起算,本來就不會補跑;不要替它造一個
    systemd_timer.forget_missed_runs("acg-keepalive.timer")

    assert not systemd_timer.stamp_path("acg-keepalive.timer").exists()
    assert [action for action, _ in systemctl] == ["stop"]


def test_the_stamp_lives_where_systemd_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    assert systemd_timer.stamp_path("acg-keepalive.timer") == (
        tmp_path / "share" / "systemd" / "timers" / "stamp-acg-keepalive.timer"
    )
