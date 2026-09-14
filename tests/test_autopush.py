"""排程只是觸發器;要不要真的推,由這裡的判斷決定。"""

import plistlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_config import autopush


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    root.mkdir(parents=True)
    monkeypatch.setattr(autopush, "memory_dir", lambda: root)
    monkeypatch.setattr(autopush, "HOME", tmp_path)
    return root


def _changes(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    monkeypatch.setattr(autopush, "_memory_has_changes", lambda: present)


def test_nothing_changed_means_nothing_to_send(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _changes(monkeypatch, False)

    decision = autopush.decide()

    assert decision.push is False
    assert "沒有變更" in decision.reason


def test_a_recent_push_is_left_alone(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 一天開五個 session 不該產生五個 commit
    _changes(monkeypatch, True)
    autopush.record_push(datetime.now(UTC) - timedelta(hours=1))

    assert autopush.decide(12).push is False


def test_an_old_enough_push_goes_again(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _changes(monkeypatch, True)
    autopush.record_push(datetime.now(UTC) - timedelta(hours=13))

    assert autopush.decide(12).push is True


def test_never_pushed_with_changes_goes(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _changes(monkeypatch, True)

    assert autopush.decide().push is True


def test_a_corrupt_state_file_does_not_stop_the_push(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _changes(monkeypatch, True)
    autopush.state_path().write_text("不是時間", encoding="utf-8")

    assert autopush.decide().push is True


def test_missing_notebook_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(autopush, "memory_dir", lambda: tmp_path / "absent")

    assert autopush.decide().push is False


def test_the_timer_survives_a_machine_that_was_asleep() -> None:
    # 四點關機的話,沒有 Persistent 就整天不會推
    _service, timer = autopush.systemd_units(4, 12)

    assert "OnCalendar=*-*-* 04:00:00" in timer
    assert "Persistent=true" in timer


def test_the_service_runs_with_the_cooldown() -> None:
    service, _timer = autopush.systemd_units(4, 12)

    assert "--if-stale" in service
    assert "12" in service


def test_the_launch_agent_asks_for_the_same_hour() -> None:
    parsed = plistlib.loads(autopush.launchd_plist(4, 12))

    assert parsed["StartCalendarInterval"] == {"Hour": 4, "Minute": 0}
    assert "--if-stale" in parsed["ProgramArguments"]
    # RunAtLoad 會在每次登入時推,那不是每天一次
    assert parsed["RunAtLoad"] is False


def test_the_windows_task_replaces_an_existing_one() -> None:
    argv = autopush.schtasks_argv(4, 12)

    assert "/F" in argv  # 沒有 /F,重複 enable 會失敗
    assert "/ST" in argv and "04:00" in argv
    assert any("--if-stale" in part for part in argv)


@pytest.mark.parametrize("hour", [-1, 24, 99])
def test_an_impossible_hour_is_refused(hour: int) -> None:
    with pytest.raises(ValueError):
        autopush.enable(hour)


def test_the_opportunistic_path_stays_out_of_the_way(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 裝了排程就不該再順手推,否則一天推兩次
    monkeypatch.setattr(autopush, "_schedule_installed", lambda: True)
    called = []
    monkeypatch.setattr(autopush, "decide", lambda *a: called.append(1))

    autopush.opportunistic_push()

    assert called == []


def test_the_opportunistic_path_can_be_switched_off(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_CONFIG_NO_AUTOPUSH", "1")
    called = []
    monkeypatch.setattr(autopush, "_schedule_installed", lambda: called.append(1))

    autopush.opportunistic_push()

    assert called == []


def test_a_failure_never_breaks_the_command_it_rode_in_on(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(autopush, "_schedule_installed", lambda: False)

    def boom(*_args: object) -> None:
        raise OSError("git 壞了")

    monkeypatch.setattr(autopush, "decide", boom)

    autopush.opportunistic_push()  # 不應拋出


def test_a_successful_push_is_recorded(notebook: Path) -> None:
    moment = datetime(2026, 9, 14, 4, 0, tzinfo=UTC)
    autopush.record_push(moment)

    assert autopush.state_path().read_text(encoding="utf-8").startswith("2026-09-14")
