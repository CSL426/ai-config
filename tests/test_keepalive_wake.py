"""After a codex quota runs out, wake that one account when it resets."""

import json
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_config import (
    keepalive_limits,
    keepalive_runner,
    keepalive_scheduler,
    keepalive_settings,
    paths,
)
from ai_config.commands.keepalive import run_keepalive

PROMPT = keepalive_settings.DEFAULT_PROMPT
META = {"type": "session_meta", "payload": {"originator": "codex_exec", "cwd": "/w"}}


def _user(text: str) -> dict:
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}],
    }}


def _limits(limit_id: str, primary, secondary) -> dict:
    return {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "limit_id": limit_id, "primary": primary, "secondary": secondary,
    }}}


def _window(used: float, minutes: int, resets_at: int) -> dict:
    return {"used_percent": used, "window_minutes": minutes, "resets_at": resets_at}


def _rollout(sessions: Path, lines: list, when: "datetime | None" = None) -> Path:
    day = (when or datetime.now().astimezone()).astimezone()
    folder = sessions / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{day:%Y-%m-%dT%H-%M-%S}-{uuid.uuid4()}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two accounts whose sessions folders link to the default home's, as on the real machine."""
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    for name in (".codex-set", ".codex-csl"):
        (home / name).mkdir()
        (home / name / "auth.json").write_text(json.dumps({"a": name}), encoding="utf-8")
        try:
            (home / name / "sessions").symlink_to(home / ".codex" / "sessions", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are not available here")
    monkeypatch.setattr(keepalive_settings, "HOME", home)
    monkeypatch.setattr(paths, "scheduled_command", lambda: ["/opt/acg"])
    return home


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every command a wake would hand the platform scheduler, none of them run."""
    seen: list = []

    def record(argv):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(keepalive_scheduler, "_wake_command", record)
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "linux")
    return seen


def _sessions(homes: Path) -> Path:
    return homes / ".codex" / "sessions"


def _log() -> str:
    try:
        return keepalive_settings.log_path("codex").read_text(encoding="utf-8")
    except OSError:
        return ""


# --- finding the call's own rollout -----------------------------------------


def test_the_calls_rollout_is_found_and_a_concurrent_user_session_ignored(homes: Path) -> None:
    sessions = _sessions(homes)
    # 昨天那次 keepalive 也帶著同一句提示,但呼叫前就在了
    _rollout(sessions, [META, _user(PROMPT)], datetime.now().astimezone() - timedelta(days=1))
    _rollout(sessions, [META, _user(PROMPT)])
    home = homes / ".codex-csl"
    before = keepalive_limits.snapshot(home)
    started = datetime.now().astimezone().timestamp()

    ours = _rollout(sessions, [META, _user(PROMPT)])
    _rollout(sessions, [META, _user("refactor the parser")])

    found, reason = keepalive_limits.find_rollout(home, PROMPT, before, started)

    assert found == ours
    assert reason == ""


def test_two_new_rollouts_with_the_prompt_record_nothing(homes: Path) -> None:
    home = homes / ".codex-set"
    before = keepalive_limits.snapshot(home)
    started = datetime.now().astimezone().timestamp()
    _rollout(_sessions(homes), [META, _user(PROMPT)])
    _rollout(_sessions(homes), [META, _user(PROMPT)])

    found, reason = keepalive_limits.find_rollout(home, PROMPT, before, started)

    assert found is None
    assert "2 rollouts" in reason


def test_a_prompt_with_quotes_is_matched_after_decoding(homes: Path) -> None:
    home = homes / ".codex-set"
    prompt = 'say "hi" — 好'
    before = keepalive_limits.snapshot(home)
    started = datetime.now().astimezone().timestamp()
    ours = _rollout(_sessions(homes), [META, _user(prompt)])

    assert keepalive_limits.find_rollout(home, prompt, before, started)[0] == ours


# --- reading the limits -----------------------------------------------------


def test_the_last_codex_limits_are_read(tmp_path: Path) -> None:
    path = _rollout(tmp_path, [
        META,
        _limits("codex", _window(1.0, 300, 100), _window(50.0, 10080, 200)),
        _limits("premium", None, None),
        "not json",
        _limits("codex", _window(2.0, 300, 1794387726), _window(52.0, 10080, 1794388227)),
        _limits("premium", None, None),
    ])
    path.write_text(path.read_text(encoding="utf-8") + "{torn", encoding="utf-8")

    assert keepalive_limits.read_limits(path) == {
        "five_hour": {"used_percent": 2.0, "resets_at": 1794387726},
        "weekly": {"used_percent": 52.0, "resets_at": 1794388227},
    }


def test_an_exhausted_call_reports_no_limits(tmp_path: Path) -> None:
    """The failed call's rate_limits are premium with null windows: no reset time in it."""
    path = _rollout(tmp_path, [META, _user(PROMPT), _limits("premium", None, None)])

    assert keepalive_limits.read_limits(path) == {}


def test_codex_limits_with_null_windows_are_skipped(tmp_path: Path) -> None:
    path = _rollout(tmp_path, [
        _limits("codex", _window(10.0, 300, 500), None),
        _limits("codex", None, None),
    ])

    assert keepalive_limits.read_limits(path) == {
        "five_hour": {"used_percent": 10.0, "resets_at": 500},
    }


# --- the decision rule ------------------------------------------------------


NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
FAR = NOW + timedelta(days=1)


def _record(five=None, weekly=None, age=timedelta(hours=1)) -> dict:
    record = {"recorded_at": int((NOW - age).timestamp())}
    for name, window in (("five_hour", five), ("weekly", weekly)):
        if window:
            used, after = window
            record[name] = {"used_percent": used, "resets_at": int((NOW + after).timestamp())}
    return record


def test_the_latest_reset_of_the_nearly_used_up_windows_wins() -> None:
    record = _record(five=(95.0, timedelta(hours=1)), weekly=(90.0, timedelta(hours=3)))

    wake, _ = keepalive_limits.decide(record, NOW, FAR)

    assert wake == NOW + timedelta(hours=3, minutes=2)


def test_a_window_just_under_ninety_percent_does_not_count() -> None:
    record = _record(five=(95.0, timedelta(hours=1)), weekly=(89.9, timedelta(hours=3)))

    wake, _ = keepalive_limits.decide(record, NOW, FAR)

    assert wake == NOW + timedelta(hours=1, minutes=2)


def test_without_a_nearly_used_up_window_the_earliest_reset_is_taken() -> None:
    record = _record(five=(40.0, timedelta(hours=2)), weekly=(60.0, timedelta(hours=1)))

    wake, _ = keepalive_limits.decide(record, NOW, FAR)

    assert wake == NOW + timedelta(hours=1, minutes=2)


def test_a_reset_at_this_very_moment_is_not_in_the_future() -> None:
    record = _record(five=(100.0, timedelta(0)), weekly=(50.0, timedelta(hours=4)))

    wake, _ = keepalive_limits.decide(record, NOW, FAR)

    assert wake == NOW + timedelta(hours=4, minutes=2)


def test_every_reset_past_means_no_wake() -> None:
    record = _record(five=(100.0, -timedelta(minutes=1)))

    wake, reason = keepalive_limits.decide(record, NOW, FAR)

    assert wake is None
    assert "already passed" in reason


@pytest.mark.parametrize("before_slot, wakes", [
    (timedelta(minutes=10), False),
    (timedelta(minutes=10, seconds=1), True),
])
def test_a_reset_close_to_the_next_scheduled_call_is_left_to_it(
    before_slot: timedelta, wakes: bool,
) -> None:
    slot = NOW + timedelta(hours=2)
    record = _record(weekly=(100.0, timedelta(hours=2) - before_slot))

    wake, reason = keepalive_limits.decide(record, NOW, slot)

    assert (wake is not None) is wakes
    if not wakes:
        assert "next scheduled call" in reason


@pytest.mark.parametrize("age, kept", [
    (timedelta(days=8), True),
    (timedelta(days=8, seconds=1), False),
])
def test_records_older_than_eight_days_are_ignored(age: timedelta, kept: bool) -> None:
    record = _record(weekly=(100.0, timedelta(hours=1)), age=age)

    wake, reason = keepalive_limits.decide(record, NOW, FAR)

    assert (wake is not None) is kept
    if not kept:
        assert "older than 8 days" in reason


@pytest.mark.parametrize("record", [None, {}, {"recorded_at": "yesterday"}, []])
def test_no_usable_record_means_no_wake(record) -> None:
    assert keepalive_limits.decide(record, NOW, FAR)[0] is None


def test_the_next_slot_rolls_over_to_tomorrow() -> None:
    now = datetime(2026, 11, 11, 23, 0).astimezone()

    slot = keepalive_limits.next_slot(("07:00", "22:15"), now)

    assert slot == datetime(2026, 11, 12, 7, 0).astimezone()


# --- the commands handed to the scheduler -----------------------------------


AT = datetime(2026, 11, 14, 9, 32).astimezone()


def test_linux_wakes_with_a_transient_timer_for_one_home(homes: Path) -> None:
    unit = keepalive_scheduler.wake_unit(".codex-csl")

    assert keepalive_scheduler.wake_systemd_argv(".codex-csl", AT) == [
        "systemd-run", "--user", f"--unit={unit}",
        "--on-calendar=2026-11-14 09:32:00", "--timer-property=AccuracySec=1min",
        "/opt/acg", "keepalive", "send", "codex", "--home", ".codex-csl",
    ]
    assert unit.startswith("acg-keepalive-codex-wake-codex-csl-")


def test_each_home_gets_its_own_name_even_when_they_look_alike() -> None:
    names = {keepalive_scheduler.wake_unit(home) for home in (".codex-a.b", ".codex-a-b", "codex-a-b")}

    assert len(names) == 3


def test_windows_registers_a_one_time_task_with_a_locale_free_time(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths, "scheduled_command", lambda: ["C:/Program Files/acg/acg.exe"])
    task = keepalive_scheduler.wake_task(".codex-csl")

    argv = keepalive_scheduler.wake_powershell_argv(".codex-csl", AT)

    assert argv == [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        ("$action = New-ScheduledTaskAction -Execute 'conhost.exe' "
        "-Argument '--headless cmd /c \"C:/Program Files/acg/acg.exe\" keepalive send codex "
        "--home .codex-csl'; "
        "$at = [datetime]::ParseExact('2026-11-14 09:32:00', 'yyyy-MM-dd HH:mm:ss', "
        "[Globalization.CultureInfo]::InvariantCulture); "
        "$trigger = New-ScheduledTaskTrigger -Once -At $at; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"Register-ScheduledTask -TaskName '{task}' "
        "-Action $action -Trigger $trigger -Settings $settings -Force | Out-Null"),
    ]
    assert task.startswith("acg keepalive codex wake codex-csl-")
    assert "schtasks" not in " ".join(argv)


def test_scheduling_again_replaces_instead_of_adding(homes: Path, scheduler: list) -> None:
    later = AT + timedelta(hours=1)

    assert keepalive_scheduler.schedule_wake(".codex-csl", AT)[0]
    assert keepalive_scheduler.schedule_wake(".codex-csl", later)[0]

    unit = keepalive_scheduler.wake_unit(".codex-csl")
    stop = ["systemctl", "--user", "stop", f"{unit}.timer"]
    reset = ["systemctl", "--user", "reset-failed", f"{unit}.timer", f"{unit}.service"]
    assert scheduler == [
        stop, reset, keepalive_scheduler.wake_systemd_argv(".codex-csl", AT),
        stop, reset, keepalive_scheduler.wake_systemd_argv(".codex-csl", later),
    ]
    wakes = keepalive_limits.read_json(keepalive_limits.wakes_path())
    assert wakes == {".codex-csl": {"at": int(later.timestamp())}}


def test_windows_replaces_with_force(
    homes: Path, scheduler: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "windows")

    keepalive_scheduler.schedule_wake(".codex-csl", AT)
    keepalive_scheduler.schedule_wake(".codex-csl", AT + timedelta(hours=1))

    assert len(scheduler) == 2
    assert all("-Force" in argv[-1] for argv in scheduler)
    assert list(keepalive_limits.read_json(keepalive_limits.wakes_path())) == [".codex-csl"]


def test_macos_is_skipped_without_touching_a_scheduler(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 沒換掉 _wake_command:碰到排程就會被 conftest 擋下來
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "macos")

    scheduled, line = keepalive_scheduler.schedule_wake(".codex-csl", AT)

    assert scheduled is False
    assert "not supported on macOS" in line
    assert keepalive_limits.read_json(keepalive_limits.wakes_path()) == {}


def test_a_home_name_unsafe_for_a_command_line_is_not_scheduled(scheduler: list) -> None:
    scheduled, _ = keepalive_scheduler.schedule_wake(".codex & calc", AT)

    assert scheduled is False
    assert scheduler == []


def test_a_failed_scheduler_call_leaves_no_pending_record(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "linux")
    monkeypatch.setattr(
        keepalive_scheduler, "_wake_command",
        lambda argv: subprocess.CompletedProcess(argv, 1, "", "Unit already exists"),
    )

    scheduled, line = keepalive_scheduler.schedule_wake(".codex-csl", AT)

    assert scheduled is False
    assert "Unit already exists" in line
    assert keepalive_limits.read_json(keepalive_limits.wakes_path()) == {}


def test_a_leftover_wake_task_does_not_read_as_an_enabled_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = {"stdout": ""}
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "windows")
    monkeypatch.setattr(
        keepalive_scheduler.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, listing["stdout"], ""),
    )

    listing["stdout"] = f"TaskName: \\{keepalive_scheduler.wake_task('.codex-csl')}\n"
    assert keepalive_scheduler.installed("codex") is False

    listing["stdout"] += "TaskName: \\acg keepalive codex 0700\n"
    assert keepalive_scheduler.installed("codex") is True


# --- send: record, cancel, plan ---------------------------------------------


def _codex(monkeypatch: pytest.MonkeyPatch, sessions: Path, outcome) -> list:
    """Fake codex: each call writes what the real one would into the shared sessions folder."""
    calls = []

    def run(argv, **kwargs):
        home = Path((kwargs.get("env") or {})["CODEX_HOME"]).name
        calls.append(home)
        return outcome(home, sessions, argv)

    monkeypatch.setattr(keepalive_runner.subprocess, "run", run)
    return calls


def _succeeds(home, sessions, argv):
    used = 52.0 if home == ".codex-csl" else 10.0
    _rollout(sessions, [
        META, _user(PROMPT),
        _limits("codex", _window(2.0, 300, 1794387726), _window(used, 10080, 1794388227)),
    ])
    # 同一時間使用者自己的 session,額度數字不同,不能被記成這個帳號的
    _rollout(sessions, [
        META, _user("explain this stack trace"),
        _limits("codex", _window(99.0, 300, 1), _window(99.0, 10080, 2)),
    ])
    return subprocess.CompletedProcess(argv, 0, "hi\n", "")


def _exhausted(home, sessions, argv):
    _rollout(sessions, [META, _user(PROMPT), _limits("premium", None, None)])
    return subprocess.CompletedProcess(argv, 1, "", "ERROR: You've hit your usage limit.\n")


def test_a_successful_call_records_each_accounts_limits(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _codex(monkeypatch, _sessions(homes), _succeeds)

    assert keepalive_runner.send("codex") == 0

    record = keepalive_limits.read_json(keepalive_limits.limits_path())
    assert sorted(record) == [".codex-csl", ".codex-set"]
    assert record[".codex-csl"]["weekly"] == {"used_percent": 52.0, "resets_at": 1794388227}
    assert record[".codex-set"]["weekly"]["used_percent"] == 10.0
    assert record[".codex-csl"]["five_hour"] == {"used_percent": 2.0, "resets_at": 1794387726}
    assert isinstance(record[".codex-csl"]["recorded_at"], int)


def test_an_exhausted_call_keeps_the_last_record(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keepalive_limits.write_json(keepalive_limits.limits_path(), {".codex-csl": {"recorded_at": 1}})
    monkeypatch.setattr(keepalive_scheduler, "installed", lambda tool="claude": False)
    _codex(monkeypatch, _sessions(homes), _exhausted)

    assert keepalive_runner.send("codex") == 0

    assert keepalive_limits.read_json(keepalive_limits.limits_path()) == {
        ".codex-csl": {"recorded_at": 1},
    }
    assert "codex keepalive is not enabled; no one-shot wake" in _log()


def test_a_garbled_record_is_logged_not_raised(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = keepalive_limits.limits_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{garbled", encoding="utf-8")
    monkeypatch.setattr(keepalive_scheduler, "installed", lambda tool="claude": True)
    _codex(monkeypatch, _sessions(homes), _exhausted)

    assert keepalive_runner.send("codex") == 0

    logged = _log()
    assert "could not read keepalive-codex-limits.json" in logged
    assert "no limits recorded from a successful call" in logged


def _soon_slot(monkeypatch: pytest.MonkeyPatch, hours: int) -> None:
    """Put the only scheduled codex call `hours` from now."""
    at = (datetime.now().astimezone() + timedelta(hours=hours)).strftime("%H:%M")
    keepalive_settings.save(keepalive_settings.Settings(times=(at,)), "codex")
    monkeypatch.setattr(keepalive_scheduler, "installed", lambda tool="claude": True)


def test_an_exhausted_account_gets_a_wake_for_itself_alone(
    homes: Path, scheduler: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = datetime.now(UTC) + timedelta(minutes=30)
    keepalive_limits.write_json(keepalive_limits.limits_path(), {".codex-csl": {
        "recorded_at": int(datetime.now(UTC).timestamp()) - 3600,
        "weekly": {"used_percent": 100.0, "resets_at": int(reset.timestamp())},
    }})
    _soon_slot(monkeypatch, 3)
    _codex(monkeypatch, _sessions(homes), _exhausted)

    assert keepalive_runner.send("codex") == 0

    runs = [argv for argv in scheduler if argv[0] == "systemd-run"]
    assert len(runs) == 1
    assert runs[0][-2:] == ["--home", ".codex-csl"]
    wakes = keepalive_limits.read_json(keepalive_limits.wakes_path())
    assert wakes == {".codex-csl": {"at": int(reset.timestamp()) + 120}}
    assert ".codex-set: no one-shot wake: no limits recorded" in _log()
    assert ".codex-csl: one-shot wake scheduled for" in _log()


def test_a_reset_after_the_next_scheduled_call_is_left_to_it(
    homes: Path, scheduler: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = datetime.now(UTC) + timedelta(days=2)
    keepalive_limits.write_json(keepalive_limits.limits_path(), {".codex-csl": {
        "recorded_at": int(datetime.now(UTC).timestamp()),
        "weekly": {"used_percent": 100.0, "resets_at": int(reset.timestamp())},
    }})
    _soon_slot(monkeypatch, 3)
    _codex(monkeypatch, _sessions(homes), _exhausted)

    keepalive_runner.send("codex")

    assert scheduler == []
    assert "not before the next scheduled call" in _log()


def test_success_cancels_only_that_accounts_pending_wake(
    homes: Path, scheduler: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    keepalive_limits.write_json(keepalive_limits.wakes_path(), {
        ".codex-csl": {"at": at}, ".codex-set": {"at": at},
    })
    calls = _codex(monkeypatch, _sessions(homes), _succeeds)

    assert run_keepalive(["send", "codex", "--home", ".codex-csl"]) == 0

    assert calls == [".codex-csl"]
    unit = keepalive_scheduler.wake_unit(".codex-csl")
    # 取消可能發生在那次喚醒自己的 service 裡,只能停 timer
    assert scheduler == [["systemctl", "--user", "stop", f"{unit}.timer"]]
    assert keepalive_limits.read_json(keepalive_limits.wakes_path()) == {".codex-set": {"at": at}}


def test_success_without_a_pending_wake_leaves_the_scheduler_alone(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 沒換掉 _wake_command:成功後若無條件去取消,conftest 會讓這裡失敗
    _codex(monkeypatch, _sessions(homes), _succeeds)

    assert keepalive_runner.send("codex") == 0


# --- --home, disable, status ------------------------------------------------


@pytest.mark.parametrize("args", [
    ["send", "codex", "--home", ".codex-nope"],
    ["send", "codex", "--home", "../.codex-csl"],
    ["send", "codex", "--home"],
    ["send", "claude", "--home", ".codex-csl"],
    ["send", "--home", ".codex-csl"],
    ["status", "codex", "--home", ".codex-csl"],
])
def test_home_must_name_a_codex_account_on_this_machine(
    homes: Path, monkeypatch: pytest.MonkeyPatch, args: list,
) -> None:
    calls = _codex(monkeypatch, _sessions(homes), _succeeds)

    assert run_keepalive(args) == 1

    assert calls == []


def test_home_given_as_one_word_is_accepted(homes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _codex(monkeypatch, _sessions(homes), _succeeds)

    assert run_keepalive(["send", "codex", "--home=.codex-set"]) == 0

    assert calls == [".codex-set"]


def test_disable_removes_every_pending_wake(
    homes: Path, scheduler: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        keepalive_scheduler.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    # .codex-old 的帳號已經刪掉了,它的喚醒還是要清
    keepalive_limits.write_json(keepalive_limits.wakes_path(), {
        ".codex-csl": {"at": 2000000000}, ".codex-old": {"at": 2000000000},
    })

    code, lines = keepalive_scheduler.disable("codex")

    assert code == 0
    assert sorted(argv[3] for argv in scheduler) == sorted(
        f"{keepalive_scheduler.wake_unit(home)}.timer" for home in (".codex-csl", ".codex-old")
    )
    assert keepalive_limits.read_json(keepalive_limits.wakes_path()) == {}
    assert any("2 個待執行的單獨喚醒" in line for line in lines)


def test_disabling_another_tool_leaves_codex_wakes(
    homes: Path, scheduler: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        keepalive_scheduler.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    keepalive_limits.write_json(keepalive_limits.wakes_path(), {".codex-csl": {"at": 2000000000}})

    keepalive_scheduler.disable("claude")

    assert scheduler == []
    assert list(keepalive_limits.read_json(keepalive_limits.wakes_path())) == [".codex-csl"]


def test_status_shows_each_accounts_weekly_quota_and_pending_wake(
    homes: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(keepalive_scheduler, "installed", lambda tool="claude": True)
    monkeypatch.setattr(keepalive_scheduler, "existing_ccs", lambda: "")
    now = datetime.now(UTC)
    reset = (now + timedelta(days=2)).replace(microsecond=0)
    wake = reset + timedelta(minutes=2)
    keepalive_limits.write_json(keepalive_limits.limits_path(), {
        ".codex-csl": {
            "recorded_at": int(now.timestamp()),
            "weekly": {"used_percent": 100.0, "resets_at": int(reset.timestamp())},
        },
        ".codex-set": {
            "recorded_at": int(now.timestamp()),
            "weekly": {"used_percent": 52.0, "resets_at": int(reset.timestamp())},
        },
    })
    keepalive_limits.write_json(keepalive_limits.wakes_path(), {
        ".codex-csl": {"at": int(wake.timestamp())},
        # 已經過了的不是「待執行」
        ".codex-set": {"at": int((now - timedelta(hours=1)).timestamp())},
    })
    log = keepalive_settings.log_path("codex")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("[t] calling codex (.codex-csl)\n[t] exit 1: usage limit\n", encoding="utf-8")

    assert run_keepalive(["status", "codex"]) == 0

    out = capsys.readouterr().out
    assert ".codex-csl: [t] exit 1: usage limit" in out
    assert f"每週已用 100%,{reset.astimezone():%m-%d %H:%M} 重置" in out
    assert f"預定 {wake.astimezone():%m-%d %H:%M} 單獨喚醒" in out
    assert ".codex-set: 每週已用 52%" in out
    assert out.count("單獨喚醒") == 1


def test_a_record_that_cannot_be_written_still_cancels_the_wake(
    homes: Path, scheduler: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keepalive_limits.write_json(keepalive_limits.wakes_path(), {".codex-csl": {"at": 2000000000}})
    # 紀錄檔的位置被資料夾佔住,寫不進去
    keepalive_limits.limits_path().mkdir(parents=True)
    _codex(monkeypatch, _sessions(homes), _succeeds)

    assert run_keepalive(["send", "codex", "--home", ".codex-csl"]) == 0

    assert keepalive_limits.read_json(keepalive_limits.wakes_path()) == {}
    assert ".codex-csl: could not handle its quota record" in _log()


def test_a_failed_cancel_keeps_the_record(tmp_path, monkeypatch) -> None:
    """A task the scheduler would not remove must stay findable for disable."""
    from ai_config import keepalive_limits, keepalive_scheduler

    monkeypatch.setattr(keepalive_limits, "wakes_path", lambda: tmp_path / "wakes.json")
    keepalive_limits.write_json(tmp_path / "wakes.json", {".codex-set": {"at": 1}})
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "linux")
    monkeypatch.setattr(
        keepalive_scheduler, "_wake_command",
        lambda argv: subprocess.CompletedProcess(argv, 1, "", "Access denied"),
    )

    message = keepalive_scheduler.cancel_wake(".codex-set")

    assert "could not cancel" in message
    assert ".codex-set" in keepalive_limits.read_json(tmp_path / "wakes.json")


def test_a_failed_reschedule_drops_the_stale_record(tmp_path, monkeypatch) -> None:
    """Linux stops the old timer first, so a failed replacement leaves nothing pending."""
    from ai_config import keepalive_limits, keepalive_scheduler

    monkeypatch.setattr(keepalive_limits, "wakes_path", lambda: tmp_path / "wakes.json")
    keepalive_limits.write_json(tmp_path / "wakes.json", {".codex-set": {"at": 1}})
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "linux")
    monkeypatch.setattr(
        keepalive_scheduler, "_wake_command",
        lambda argv: subprocess.CompletedProcess(argv, 1, "", "boom"),
    )

    scheduled, _ = keepalive_scheduler.schedule_wake(
        ".codex-set", datetime.now().astimezone() + timedelta(hours=1),
    )

    assert scheduled is False
    assert keepalive_limits.read_json(tmp_path / "wakes.json") == {}


def test_an_unreadable_record_is_not_overwritten(tmp_path, monkeypatch) -> None:
    from ai_config import keepalive_limits

    path = tmp_path / "limits.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(keepalive_limits, "limits_path", lambda: path)

    with pytest.raises(OSError):
        keepalive_limits.remember(".codex-csl", {"weekly": {"used_percent": 1}})

    assert path.read_text(encoding="utf-8") == "{broken"


def test_a_window_missing_from_a_later_call_is_kept(tmp_path, monkeypatch) -> None:
    from ai_config import keepalive_limits

    path = tmp_path / "limits.json"
    monkeypatch.setattr(keepalive_limits, "limits_path", lambda: path)
    keepalive_limits.remember(".codex-csl", {"weekly": {"used_percent": 90, "resets_at": 5}})
    keepalive_limits.remember(".codex-csl", {"five_hour": {"used_percent": 1, "resets_at": 6}})

    record = keepalive_limits.read_json(path)[".codex-csl"]
    assert record["weekly"]["resets_at"] == 5
    assert record["five_hour"]["resets_at"] == 6
