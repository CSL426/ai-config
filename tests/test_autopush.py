"""排程只是觸發器;要不要真的推,由這裡的判斷決定。"""

import plistlib
import subprocess
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


def test_being_behind_is_caught_up_automatically(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 多台機器同一時間醒來,醒來時都落後;能接上就繼續推
    _changes(monkeypatch, True)
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: True)
    monkeypatch.setattr(autopush, "_catch_up", lambda: True)

    assert autopush.decide().push is True


def test_a_conflict_is_left_for_a_person(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 無人看管時解衝突可能默默丟掉別人的筆記,寧可停下
    _changes(monkeypatch, True)
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: True)
    monkeypatch.setattr(autopush, "_catch_up", lambda: False)

    decision = autopush.decide()

    assert decision.push is False
    assert "acg pull" in decision.reason


def test_git_never_waits_for_a_password_when_nobody_is_there() -> None:
    """排程沒有終端機;git 停下來問帳密就會靜止到工作被砍掉。

    Windows 上實際發生過:行程活了 12 分鐘只燒 0.7 秒 CPU,其餘時間
    都在等 stdin。要明確失敗,不要等。
    """
    from ai_config.commands import sync

    captured = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    original = sync.subprocess.run
    sync.subprocess.run = fake_run
    try:
        sync._run_repo_git("status")
    finally:
        sync.subprocess.run = original

    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["timeout"] > 0


def test_the_windows_task_goes_through_cmd() -> None:
    """直接啟動 exe 時工作排程器給的標準控制代碼不堪用,行程會停在那裡。

    實測:直接啟動掛住不動,同一條命令列包一層 cmd 1.5 秒就完成。
    """
    argv = autopush.schtasks_argv(4, 12)
    command = argv[argv.index("/TR") + 1]

    assert command.startswith("cmd /c ")
    assert "--if-stale" in command


def test_every_platform_caps_how_long_one_run_may_take() -> None:
    """卡住的行程要被砍掉,不要佔到下一次排程。

    Windows 的預設是 72 小時,一個停住的工作會掛到後天。
    """
    service, _timer = autopush.systemd_units(4, 12)
    assert "RuntimeMaxSec=" in service

    parsed = plistlib.loads(autopush.launchd_plist(4, 12))
    assert parsed["ExitTimeOut"] > 0


def test_windows_sets_the_limit_after_creating_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # schtasks /Create 沒有表達執行上限的參數,要另外設
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(autopush.subprocess, "run", fake_run)
    monkeypatch.setattr(autopush, "platform_name", lambda: "windows")

    lines = autopush.enable(4)

    assert any("powershell" in str(call) for call in seen)
    assert any("ExecutionTimeLimit" in str(call) for call in seen)
    assert any("15 分鐘" in line for line in lines)


def test_the_remote_is_asked_even_with_nothing_to_send(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沒有變更的機器也要 fetch,否則它的遠端快照會無限期變舊。

    實際遇到:一台機器兩天沒推過任何東西,origin/main 也就停在兩天前,
    落後判斷拿舊快照去比,永遠說「一致」。
    """
    asked = []
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: asked.append(1) or False)
    _changes(monkeypatch, False)

    autopush.decide()

    assert asked == [1]


def test_a_changed_slot_moves_the_schedule_by_itself(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """表在別台改,這台自己跟上;不用每台重跑 enable。"""
    from ai_config import schedule_table

    monkeypatch.setattr(schedule_table, "memory_dir", lambda: notebook)
    monkeypatch.setattr(schedule_table, "host_name", lambda: "mine")
    schedule_table.record("mine", schedule_table.Slot(4, 30))
    monkeypatch.setattr(autopush, "_schedule_installed", lambda: True)
    monkeypatch.setattr(autopush, "_scheduled_at", lambda: (4, 0))
    asked = []
    monkeypatch.setattr(autopush, "enable", lambda hour: asked.append(hour))

    message = autopush.reconcile_slot()

    assert asked == [4]
    assert "04:30" in message


def test_an_unchanged_slot_leaves_the_schedule_alone(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config import schedule_table

    monkeypatch.setattr(schedule_table, "memory_dir", lambda: notebook)
    monkeypatch.setattr(schedule_table, "host_name", lambda: "mine")
    schedule_table.record("mine", schedule_table.Slot(4, 30))
    monkeypatch.setattr(autopush, "_schedule_installed", lambda: True)
    monkeypatch.setattr(autopush, "_scheduled_at", lambda: (4, 30))
    monkeypatch.setattr(
        autopush, "enable", lambda hour: pytest.fail("時段沒變不該重建排程")
    )

    assert autopush.reconcile_slot() == ""


def test_no_schedule_means_nothing_to_reconcile(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config import schedule_table

    monkeypatch.setattr(schedule_table, "memory_dir", lambda: notebook)
    monkeypatch.setattr(schedule_table, "host_name", lambda: "mine")
    schedule_table.record("mine", schedule_table.Slot(4, 30))
    monkeypatch.setattr(autopush, "_schedule_installed", lambda: False)
    monkeypatch.setattr(
        autopush, "enable", lambda hour: pytest.fail("沒裝排程不該去建")
    )

    assert autopush.reconcile_slot() == ""


def test_a_quiet_machine_still_catches_up(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沒東西要推的機器也要接上遠端。

    共用的時間表住在記憶目錄裡,永遠不接上的機器會一直讀到舊的一份,
    看不到別台認領了哪一分鐘,於是大家永遠撞在一起。
    """
    _changes(monkeypatch, False)
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: True)
    caught = []
    monkeypatch.setattr(autopush, "_catch_up", lambda: caught.append(1) or True)

    autopush.decide()

    assert caught == [1]
