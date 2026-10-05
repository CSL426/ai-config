"""排程只是觸發器;要不要真的推,由這裡的判斷決定。"""

import plistlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_config import autopush


@pytest.fixture(autouse=True)
def _never_the_real_data_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    # 這裡的 decide() 曾經對這台真的資料庫做 rebase --autostash,
    # 把它卡在衝突裡三天。要模擬落後或接上的測試會自己蓋掉這兩個
    def refuse() -> bool:
        raise AssertionError("a test reached the real data repository")

    monkeypatch.setattr(autopush, "_behind_upstream", lambda: False)
    monkeypatch.setattr(autopush, "_catch_up", refuse)


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    root.mkdir(parents=True)
    monkeypatch.setattr(autopush, "memory_dir", lambda: root)
    monkeypatch.setattr(autopush, "HOME", tmp_path)
    # 狀態檔改放本機之後,沒有這行測試會寫進真的家目錄
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
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
    notebook: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _changes(monkeypatch, True)
    autopush.state_path().parent.mkdir(parents=True, exist_ok=True)
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
    from ai_config import schedule_table

    schedule_table.record("test-host", schedule_table.Slot(4, 10))
    with pytest.raises(ValueError):
        autopush.install(hour)
    # 拒絕就不能留下痕跡,否則表項會壞掉、下次從頭認領
    assert schedule_table.load().hosts["test-host"] == schedule_table.Slot(4, 10)


def test_the_table_never_points_at_the_real_notebook(tmp_path: Path) -> None:
    from ai_config import schedule_table

    assert tmp_path in schedule_table.table_dir().parents


def test_the_opportunistic_path_stays_out_of_the_way(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 開了每晚上傳就不該再順手推,否則一天推兩次
    from ai_config import nightly

    monkeypatch.setattr(nightly, "enabled", lambda feature: feature == "autopush")
    called = []
    monkeypatch.setattr(autopush, "decide", lambda *a: called.append(1))

    autopush.opportunistic_push()

    assert called == []


def test_the_opportunistic_path_can_be_switched_off(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_CONFIG_NO_AUTOPUSH", "1")
    called = []
    monkeypatch.setattr(autopush, "decide", lambda *a: called.append(1))

    autopush.opportunistic_push()

    assert called == []


def test_a_failure_never_breaks_the_command_it_rode_in_on(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(autopush, "schedule_installed", lambda: False)

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

    # conhost --headless:Windows Terminal 當預設主控台時,只有 cmd 會開一個看得到的視窗
    assert command.startswith("conhost.exe --headless cmd /c ")
    assert "--if-stale" in command
    # 沒有 journal 的 Windows,失敗的那一晚只剩結束碼;輸出要留下來
    log = autopush._log_path()
    assert command.endswith((f"> {log} 2>&1", f'> "{log}" 2>&1'))
    # 日誌不能在要推上去的記憶目錄裡
    assert not autopush._log_path().is_relative_to(autopush.memory_dir())


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

    lines = autopush.install(4)

    assert any("powershell" in str(call) for call in seen)
    assert any("PT60M" in str(call) for call in seen)
    # 每晚排程也要更新四個工具,十五分鐘不夠
    assert any("60 分鐘" in line for line in lines)


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
    monkeypatch.setattr(autopush, "schedule_installed", lambda: True)
    monkeypatch.setattr(autopush, "_scheduled_at", lambda: (4, 0))
    asked = []
    monkeypatch.setattr(autopush, "install", lambda hour: asked.append(hour))

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
    monkeypatch.setattr(autopush, "schedule_installed", lambda: True)
    monkeypatch.setattr(autopush, "_scheduled_at", lambda: (4, 30))
    monkeypatch.setattr(
        autopush, "install", lambda hour: pytest.fail("時段沒變不該重建排程")
    )

    assert autopush.reconcile_slot() == ""


def test_no_schedule_means_nothing_to_reconcile(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config import schedule_table

    monkeypatch.setattr(schedule_table, "memory_dir", lambda: notebook)
    monkeypatch.setattr(schedule_table, "host_name", lambda: "mine")
    schedule_table.record("mine", schedule_table.Slot(4, 30))
    monkeypatch.setattr(autopush, "schedule_installed", lambda: False)
    monkeypatch.setattr(
        autopush, "install", lambda hour: pytest.fail("沒裝排程不該去建")
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


def test_the_push_timestamp_stays_on_this_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每台的上次推送時間不同,同步會讓一台的時間變成另一台的。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(autopush, "memory_dir", lambda: tmp_path / "memory")

    path = autopush.state_path()

    assert (tmp_path / "memory") not in path.parents
    autopush.record_push()
    assert path.is_file()


def test_enable_moves_off_a_slot_somebody_else_holds(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """撞到別台時 enable 就該讓位,不要等到隔天排程執行才錯開。"""
    from ai_config import schedule_table

    monkeypatch.setattr(schedule_table, "memory_dir", lambda: notebook)
    monkeypatch.setattr(schedule_table, "host_name", lambda: "zulu")
    schedule_table.record("alpha", schedule_table.Slot(4, 0))
    schedule_table.record("zulu", schedule_table.Slot(4, 0))

    slot = autopush._claim_slot(None)

    assert str(slot) == "04:10"


def test_bare_enable_asks_the_table_for_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不帶參數的 enable 要讓時間表分配,不是直接用預設小時。

    傳了預設值就會走「手動指定」那條路,讓位邏輯整個不會執行。
    """
    from ai_config.commands import memory as command

    seen = []
    monkeypatch.setattr(autopush, "enable", lambda hour=None: seen.append(hour) or [])

    command._autopush(["enable"])

    assert seen == [None]


def test_the_installed_schedule_keeps_its_names() -> None:
    """Three machines already have a schedule installed under these names.

    Renaming a unit does not move the old one: the machine keeps running
    the orphan on its old timer and enable installs a second alongside it,
    so the push happens twice and neither name is the one anyone looks for.
    """
    service, timer = autopush.systemd_units(4, 12.0, minute=10)

    assert autopush._UNIT == "acg-autopush"
    assert autopush._LABEL == "com.csl426.acg.autopush"
    assert autopush._TASK == "acg memory autopush"
    assert "OnCalendar=*-*-* 04:10:00" in timer
    assert "Persistent=true" in timer
    assert "__nightly --if-stale 12" in service


def test_a_blocked_push_leaves_a_reason_until_one_succeeds(
    notebook: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """2026-09-30: blocked at 04:18, and status kept showing the last success."""
    import sys

    # 被擋下的內容還在,等著下一次推
    _changes(monkeypatch, True)
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: False)

    def blocked() -> int:
        print("\033[0;31m✗\033[0m Potential credential content would be committed; push cancelled:",
              file=sys.stderr)
        print("  memory/handoff/acg 排程.md")
        print("  memory/topics/deploy.md")
        print("ℹ False positive (docs/examples)? Re-run with ...")
        return 1

    assert autopush.push_and_record(blocked) == 1

    failure = autopush.last_failure()
    assert failure["reason"] == "Potential credential content would be committed; push cancelled:"
    assert failure["paths"] == ["memory/handoff/acg 排程.md", "memory/topics/deploy.md"]
    # 輸出照樣進 journal,不能被側錄吃掉
    assert "Potential credential" in capsys.readouterr().err
    assert autopush.status()["last_failure"] == failure

    assert autopush.push_and_record(lambda: 0) == 0
    assert autopush.last_failure() is None


def test_a_failure_without_an_error_line_still_says_something(notebook: Path) -> None:
    assert autopush.push_and_record(lambda: 3) == 3
    assert autopush.last_failure()["reason"] == "結束碼 3"


def test_nothing_left_to_push_clears_an_old_failure(
    notebook: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixed by a manual push or by removing the note: nothing pending, nothing to report."""
    assert autopush.push_and_record(lambda: 1) == 1
    _changes(monkeypatch, True)
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: False)
    autopush.decide()
    assert autopush.last_failure() is not None

    _changes(monkeypatch, False)
    autopush.decide()
    assert autopush.last_failure() is None


def test_an_unreadable_status_keeps_the_failure(
    notebook: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git status failing is not 'nothing to push'; the notice must survive it."""
    assert autopush.push_and_record(lambda: 1) == 1
    monkeypatch.setattr(autopush, "_memory_has_changes", lambda: None)
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: False)

    decision = autopush.decide()

    assert decision.push is False
    assert autopush.last_failure() is not None


def test_the_scheduled_push_adopts_touched_projects_first(
    notebook: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adopting first puts a newly found project's journal into the same push."""
    from ai_config.commands import memory as command
    from ai_config.commands import memory_lifecycle

    order = []
    monkeypatch.setattr(memory_lifecycle, "adopt_touched", lambda: order.append("adopt") or [])
    monkeypatch.setattr(
        autopush, "decide", lambda stale: order.append("decide") or autopush.Decision(False, "x"),
    )
    monkeypatch.setattr(autopush, "reconcile_slot", lambda: "")

    assert command.run_memory(["push", "--if-stale", "12"]) == 0

    assert order == ["adopt", "decide"]


def test_a_task_made_before_headless_is_rewritten_at_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autopush, "platform_name", lambda: "windows")
    monkeypatch.setattr(autopush, "schedule_installed", lambda: True)
    monkeypatch.setattr(autopush, "_scheduled_at", lambda: (4, 0))
    xml = {"text": "<Command>cmd</Command><Arguments>/c ai-config.exe __nightly</Arguments>"}
    monkeypatch.setattr(autopush.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, xml["text"], ""))
    rebuilt = []
    monkeypatch.setattr(autopush, "install", lambda hour: rebuilt.append(hour) or [])

    assert "04:00" in autopush.refresh_windows_task()
    assert rebuilt == [4]

    # 不開視窗但沒寫日誌的(1.0.106 建的)也要重建
    xml["text"] = "<Command>conhost.exe</Command><Arguments>--headless cmd /c x</Arguments>"
    assert "04:00" in autopush.refresh_windows_task()
    assert rebuilt == [4, 4]

    xml["text"] = ("<Command>conhost.exe</Command>"
                   "<Arguments>--headless cmd /c x > C:\\s\\nightly.log 2>&1</Arguments>")
    assert autopush.refresh_windows_task() == ""
    assert rebuilt == [4, 4]


def test_status_previews_without_rebasing_or_clearing_the_failure(
    notebook: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 實測:看一眼 status,data repo 就被 rebase 了。查狀態不該動任何東西。"""
    assert autopush.push_and_record(lambda: 1) == 1
    monkeypatch.setattr(autopush, "_behind_upstream", lambda: True)
    caught: list = []
    monkeypatch.setattr(autopush, "_catch_up", lambda: caught.append(1) or True)
    monkeypatch.setattr(autopush, "schedule_installed", lambda: False)
    _changes(monkeypatch, False)

    report = autopush.status()

    assert caught == []
    assert report["reason"] == "記憶沒有變更"
    assert autopush.last_failure() is not None
