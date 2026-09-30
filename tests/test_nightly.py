"""One nightly schedule, two switches: update the tools, then save memory with the new acg."""

import plistlib
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from ai_config import autopush, autoupdate, nightly, paths


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(autopush, "platform_name", lambda: "linux")
    monkeypatch.setattr(autopush, "_systemctl", Mock(return_value=Mock(returncode=0, stderr="")))
    monkeypatch.setattr(autopush, "forget_missed_runs", lambda timer: None)
    # 時間表住在真的記憶目錄裡,測試不能去認領時段
    monkeypatch.setattr(autopush, "_claim_slot", lambda hour: None)
    monkeypatch.setattr(paths, "scheduled_command", lambda: ["acg"])
    return tmp_path


def timer(machine: Path) -> Path:
    return machine / "config" / "systemd" / "user" / "acg-autopush.timer"


def test_a_schedule_from_before_the_merge_still_only_pushes(machine: Path) -> None:
    """Three machines have this timer and no settings file; nothing changes for them."""
    autopush.install(4)
    (machine / "state" / "acg" / "nightly.json").unlink(missing_ok=True)

    assert nightly.enabled("autopush") is True
    assert nightly.enabled("autoupdate") is False


def test_updates_alone_do_not_turn_on_the_memory_push(machine: Path) -> None:
    """Replacing executables and uploading notes are separate things to agree to."""
    autoupdate.enable()

    assert timer(machine).is_file()
    assert nightly.enabled("autoupdate") is True
    assert nightly.enabled("autopush") is False
    assert autopush.status()["installed"] is False
    assert autopush.status()["scheduled"] is True


def test_the_schedule_stays_while_either_half_wants_it(machine: Path) -> None:
    autopush.enable(4)
    autoupdate.enable()

    lines = autopush.disable()

    assert timer(machine).is_file()
    assert nightly.enabled("autoupdate") is True
    assert "每天自動更新" in lines[0]

    autoupdate.disable()

    assert not timer(machine).exists()
    assert nightly.enabled("autoupdate") is False


def test_the_timer_runs_the_nightly_entry_with_room_for_updates(machine: Path) -> None:
    service, timer_text = autopush.systemd_units(4, 12, minute=20)

    assert "ExecStart=acg __nightly --if-stale 12" in service
    # 更新四個工具要幾分鐘,原本十五分鐘的上限不夠
    assert "RuntimeMaxSec=3600" in service
    assert "OnCalendar=*-*-* 04:20:00" in timer_text
    assert "Persistent=true" in timer_text
    argv = autopush.schtasks_argv(4, 12, 20)
    assert argv[argv.index("/TR") + 1] == "conhost.exe --headless cmd /c acg __nightly --if-stale 12"
    parsed = plistlib.loads(autopush.launchd_plist(4, 12, 20))
    assert parsed["ProgramArguments"] == ["acg", "__nightly", "--if-stale", "12"]
    assert parsed["ExitTimeOut"] == 3600


def _record_night(monkeypatch: pytest.MonkeyPatch, update_code: int = 0) -> list:
    order = []
    monkeypatch.setattr(autoupdate, "run", lambda: order.append("update") or update_code)

    def push(argv, **kwargs):
        order.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(nightly.subprocess, "run", push)
    return order


def test_the_push_runs_after_the_update_with_the_installed_acg(
    machine: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine sat blocked a night after the fix was out; updating first fixes that."""
    autopush.enable(4)
    autoupdate.enable()
    order = _record_night(monkeypatch)

    assert nightly.run(12) == 0

    assert order == ["update", ["acg", "memory", "push", "--if-stale", "12"]]


def test_a_failed_update_does_not_skip_the_push(
    machine: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    autopush.enable(4)
    autoupdate.enable()
    order = _record_night(monkeypatch, update_code=1)

    assert nightly.run(12) == 1

    assert order[-1][:3] == ["acg", "memory", "push"]


@pytest.mark.parametrize(("on", "expected"), [
    ("autopush", ["push"]),
    ("autoupdate", ["update"]),
])
def test_each_half_runs_only_when_it_is_on(
    machine: Path, monkeypatch: pytest.MonkeyPatch, on: str, expected: list,
) -> None:
    (autopush.enable if on == "autopush" else autoupdate.enable)()
    order = _record_night(monkeypatch)

    nightly.run(12)

    assert ["update" if step == "update" else "push" for step in order] == expected


def test_a_timer_systemd_did_not_load_leaves_nothing_behind(
    machine: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(autopush, "_systemctl", Mock(return_value=Mock(returncode=1, stderr="no bus")))

    with pytest.raises(RuntimeError, match="no bus"):
        autoupdate.enable()

    assert not timer(machine).exists()
    assert nightly.enabled("autoupdate") is False


def test_a_timer_that_would_not_stop_keeps_its_files(
    machine: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    autoupdate.enable()
    monkeypatch.setattr(
        autopush, "_systemctl", Mock(return_value=Mock(returncode=1, stderr="Failed to connect to bus")),
    )

    with pytest.raises(RuntimeError, match="bus"):
        autoupdate.disable()

    assert timer(machine).is_file()
    assert nightly.enabled("autoupdate") is True


def test_moving_the_time_does_not_flip_either_switch(machine: Path) -> None:
    autoupdate.enable()

    autopush.install(6)

    assert nightly.enabled("autopush") is False
    assert nightly.enabled("autoupdate") is True
    assert autoupdate.status()["time"] == "06:00"


def test_the_opportunistic_push_still_covers_a_machine_without_autopush(
    machine: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timer that only updates must not silence the push that rides on commands."""
    autoupdate.enable()
    asked = []
    monkeypatch.setattr(autopush, "decide", lambda: asked.append(1) or autopush.Decision(False, "x"))

    autopush.opportunistic_push()

    assert asked == [1]
