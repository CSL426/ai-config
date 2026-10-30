"""Anchoring the usage window: chosen times, one throwaway call each."""

from pathlib import Path

import pytest

from ai_config import (
    keepalive_runner,
    keepalive_scheduler,
    keepalive_settings,
    keepalive_window,
)


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path / "acg"


def test_times_are_ordered_and_deduplicated() -> None:
    parsed = keepalive_settings.parse_times(["22:15", "07:00", "22:15", "12:05"])

    assert parsed.times == ["07:00", "12:05", "22:15"]
    assert parsed.rejected == []


def test_an_unusable_time_is_reported_not_dropped() -> None:
    """Scheduling three of the four times somebody asked for is worse than refusing."""
    parsed = keepalive_settings.parse_times(["07:00", "25:00", "noon", "12:5"])

    assert parsed.times == ["07:00"]
    assert parsed.rejected == ["25:00", "noon", "12:5"]


def test_settings_survive_a_round_trip(state: Path) -> None:
    keepalive_settings.save(keepalive_settings.Settings(times=("06:30", "11:35"), model="m"))

    loaded = keepalive_settings.load()

    assert loaded.times == ("06:30", "11:35")
    assert loaded.model == "m"


def test_a_broken_config_falls_back_to_defaults(state: Path) -> None:
    """A machine with an unreadable file should still anchor its window."""
    path = keepalive_settings.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert keepalive_settings.load().times == keepalive_settings.DEFAULT_TIMES


def test_the_call_names_the_model_and_prompt(state: Path) -> None:
    keepalive_settings.save(keepalive_settings.Settings(model="haiku", prompt="hi", claude_path="/c"))

    assert keepalive_settings.run_args() == ["/c", "--model", "haiku", "-p", "hi"]


def test_a_failed_call_is_logged_and_does_not_raise(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing one costs a later window, which is not worth failing over."""
    def explode(*args, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(keepalive_runner.subprocess, "run", explode)

    assert keepalive_runner.send() == 1
    assert "failed to start" in keepalive_settings.log_path().read_text(encoding="utf-8")


def test_the_schedule_covers_every_chosen_time(state: Path) -> None:
    service, timer = keepalive_scheduler.systemd_units(("07:00", "12:05"))

    assert "OnCalendar=*-*-* 07:00:00" in timer
    assert "OnCalendar=*-*-* 12:05:00" in timer
    # 機器睡過那個時間就整天錯位,而且日誌少一筆沒人會去數
    assert "Persistent=true" in timer
    assert "keepalive" in service


def test_windows_gets_one_task_per_time(state: Path) -> None:
    """schtasks has no repeating-daily-times form, so each time is its own task."""
    argv = keepalive_scheduler.schtasks_argv(("07:00", "22:15"))

    names = [a[a.index("/TN") + 1] for a in argv]
    assert names == ["acg keepalive 0700", "acg keepalive 2215"]
    assert all("/ST" in a for a in argv)


def test_launchd_lists_every_time(state: Path) -> None:
    import plistlib

    plist = plistlib.loads(keepalive_scheduler.launchd_plist(("07:00", "12:05")))

    assert plist["StartCalendarInterval"] == [
        {"Hour": 7, "Minute": 0}, {"Hour": 12, "Minute": 5},
    ]


def test_enable_refuses_while_claude_scheduler_is_installed(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both anchor the same window, so leaving the old one doubles every call."""
    monkeypatch.setattr(keepalive_scheduler, "existing_ccs", lambda: "crontab(...)")

    code, lines = keepalive_scheduler.enable(("07:00",))

    assert code == 1
    assert any("claude-scheduler" in line for line in lines)
    assert any("一天燒兩倍" in line for line in lines)
    # 拒絕就不該留下半套:設定不能被寫進去
    assert not keepalive_settings.config_path().is_file()


def test_enable_rejects_a_time_it_cannot_read(state: Path) -> None:
    code, lines = keepalive_scheduler.enable(("07:00", "half past nine"))

    assert code == 1
    assert any("half past nine" in line for line in lines)


def test_too_many_times_are_refused(state: Path) -> None:
    code, _ = keepalive_scheduler.enable(tuple(f"{h:02d}:00" for h in range(9)))

    assert code == 1


def test_each_tool_keeps_its_own_times(state: Path) -> None:
    """Three windows with no reason to share a boundary.

    Tying them to one list would move all three whenever one needed a
    different hour.
    """
    keepalive_settings.save(keepalive_settings.Settings(times=("07:00",)), tool="claude")
    keepalive_settings.save(keepalive_settings.Settings(times=("08:30",)), tool="codex")

    assert keepalive_settings.load("claude").times == ("07:00",)
    assert keepalive_settings.load("codex").times == ("08:30",)
    assert keepalive_settings.load("agy").times == keepalive_settings.DEFAULT_TIMES


def test_every_tool_calls_its_own_binary_the_cheap_way(state: Path) -> None:
    """Weakest model, least thinking: the call exists to have happened."""
    claude = keepalive_settings.run_args(tool="claude")
    codex = keepalive_settings.run_args(tool="codex")
    agy = keepalive_settings.run_args(tool="agy")

    assert "--model" in claude and "-p" in claude
    assert "exec" in codex and "model_reasoning_effort=" in " ".join(codex)
    # agy 不帶 effort 旗標:見 test_agy_does_not_pass_an_unsupported_flag
    assert "-p" in agy and agy[0].endswith("agy")


def test_an_unknown_tool_is_refused(state: Path) -> None:
    with pytest.raises(ValueError, match="不認得"):
        keepalive_settings.run_args(tool="gemini")


def test_enabling_one_tool_leaves_the_others_alone(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each tool has its own schedule; enabling codex must not disturb claude."""
    monkeypatch.setattr(keepalive_scheduler, "existing_ccs", lambda: "")
    monkeypatch.setattr(keepalive_scheduler, "_enable_systemd", lambda times, tool: ["ok"])
    monkeypatch.setattr(keepalive_scheduler, "platform_name", lambda: "linux")

    keepalive_scheduler.enable(("07:00",), tool="claude")
    keepalive_scheduler.enable(("09:00",), tool="codex")

    assert keepalive_settings.load("claude").times == ("07:00",)
    assert keepalive_settings.load("codex").times == ("09:00",)


def test_codex_asks_for_an_effort_the_api_accepts(state: Path) -> None:
    """minimal reads like the cheapest choice and the API rejects it.

    Three scheduled calls failed with "'minimal' is not supported with
    the 'gpt-6-astra' model. Supported values are: 'low', 'medium',
    'high', 'xhigh', and 'max'." The log said only "exit 1: (no output)",
    so nothing pointed at the flag.
    """
    joined = " ".join(keepalive_settings.run_args(tool="codex"))

    assert "model_reasoning_effort=minimal" not in joined
    assert "model_reasoning_effort=low" in joined


def test_agy_does_not_pass_an_unsupported_flag(state: Path) -> None:
    """--effort worked until the model behind agy changed under it.

    "invalid model selection (--model \"\" --effort \"low\"): --effort is
    not supported for the current model" — the call fails on the flag
    before it ever reaches the model, and the log only says exit 1.
    A keepalive asks for nothing but the cheapest reply available, so
    naming an effort buys nothing and breaks when the default moves.
    """
    assert "--effort" not in keepalive_settings.run_args(tool="agy")


def _finished(returncode: int, stdout: str = "", stderr: str = ""):
    import subprocess

    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


def test_a_failure_logs_why(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """exit 1 alone never said why, so every failure meant rerunning by hand.

    codex had been failing for two days on an exhausted usage limit, and
    the log showed only "exit 1: (no output)" each time. The reason was
    on stderr the whole while.
    """
    monkeypatch.setattr(keepalive_runner.subprocess, "run", _finished(
        1, stderr="hook: SessionStart\nERROR: You've hit your usage limit. "
        "try again at 12:45 PM.\n",
    ))

    # 額度用完代表視窗已經在跑,不算排程失敗
    assert keepalive_runner.send() == 0

    logged = keepalive_settings.log_path().read_text(encoding="utf-8")
    assert "usage limit" in logged
    assert "12:45" in logged
    assert "its window is already running" in logged


def test_a_failure_other_than_the_usage_limit_still_fails(
    state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keepalive_runner.subprocess, "run", _finished(
        1, stderr="ERROR: not logged in\n",
    ))

    assert keepalive_runner.send() == 1


def test_the_error_line_wins_over_trailing_noise(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools print banners and hook chatter after the error; the log needs the error."""
    monkeypatch.setattr(keepalive_runner.subprocess, "run", _finished(
        1, stderr="ERROR: model not found\nhook: Stop\nhook: Stop Completed\n",
    ))

    keepalive_runner.send()

    assert "model not found" in keepalive_settings.log_path().read_text(encoding="utf-8")


def test_a_success_still_logs_the_reply(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keepalive_runner.subprocess, "run", _finished(
        0, stdout="hi\n", stderr="some warning about config\n",
    ))

    keepalive_runner.send()

    last = keepalive_settings.log_path().read_text(encoding="utf-8").strip().splitlines()[-1]
    assert last.endswith("exit 0: hi")


def test_the_window_claude_reports_is_remembered(state: Path) -> None:
    """The status line is the only place the real reset time shows up.

    A reset four hours off the schedule could only be spotted by reading
    it off the screen; nothing kept it, so nothing could compare it.
    """
    from datetime import datetime

    reset = datetime(2026, 9, 23, 14, 50).astimezone()
    keepalive_window.record_window({"rate_limits": {"five_hour": {"resets_at": int(reset.timestamp())}}})

    start, end = keepalive_window.current_window(now=datetime(2026, 9, 23, 10, 27).astimezone())
    assert (start.hour, start.minute) == (9, 50)
    assert (end.hour, end.minute) == (14, 50)


def test_an_iso_reset_time_is_read_too(state: Path) -> None:
    keepalive_window.record_window(
        {"rate_limits": {"five_hour": {"resets_at": "2026-09-23T06:50:00Z"}}}
    )

    window = keepalive_window.current_window(
        now=datetime_utc(2026, 9, 23, 2, 27)
    )
    assert window is not None
    assert window[1] == datetime_utc(2026, 9, 23, 6, 50)


def test_a_payload_without_limits_records_nothing(state: Path) -> None:
    keepalive_window.record_window({"model": {"id": "x"}})

    assert keepalive_window.current_window() is None


def test_a_window_off_the_schedule_is_called_out(state: Path) -> None:
    """07:00 was the anchor; a window starting 09:50 means the call anchored nothing."""
    from datetime import datetime

    now = datetime(2026, 9, 23, 10, 27).astimezone()
    start = datetime(2026, 9, 23, 9, 50).astimezone()

    assert keepalive_window.drift(start, ("07:00", "12:05"), now) == "07:00"


def test_a_window_on_the_schedule_is_not(state: Path) -> None:
    from datetime import datetime

    now = datetime(2026, 9, 23, 10, 27).astimezone()
    start = datetime(2026, 9, 23, 7, 0).astimezone()

    assert keepalive_window.drift(start, ("07:00", "12:05"), now) == ""


def datetime_utc(*parts: int):
    from datetime import UTC, datetime

    return datetime(*parts, tzinfo=UTC)


def test_status_shows_the_window_and_its_drift(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import datetime, timedelta

    from ai_config.commands import keepalive as command

    now = datetime.now().astimezone()
    anchor = (now - timedelta(hours=1)).replace(second=0, microsecond=0)
    keepalive_settings.save(keepalive_settings.Settings(times=(anchor.strftime("%H:%M"),)))
    late = now + timedelta(hours=3)
    keepalive_window.record_window({"rate_limits": {"five_hour": {"resets_at": int(late.timestamp())}}})
    monkeypatch.setattr(keepalive_scheduler, "installed", lambda tool="claude": True)

    command._report("claude")

    out = capsys.readouterr().out
    assert f"目前視窗 {(late - keepalive_window.WINDOW):%H:%M}–{late:%H:%M}" in out
    assert f"不是從排程的 {anchor:%H:%M} 開始" in out


def test_without_a_schedule_there_is_no_drift_to_report(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DESKTOP-CAO4ENQ: 「未啟用」 above a warning about a scheduled time that does not exist."""
    from datetime import datetime, timedelta

    from ai_config.commands import keepalive as command

    late = datetime.now().astimezone() + timedelta(hours=3)
    keepalive_window.record_window({"rate_limits": {"five_hour": {"resets_at": int(late.timestamp())}}})
    monkeypatch.setattr(keepalive_scheduler, "installed", lambda tool="claude": False)
    # 不管現在幾點:只要拿預設時間去比,就會報出偏移
    monkeypatch.setattr(keepalive_window, "drift", lambda start, times, now: "12:05" if times else "")

    command._report("claude")

    out = capsys.readouterr().out
    assert "未啟用" in out and "目前視窗" in out
    assert "不是從排程" not in out


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with codex accounts laid out the way the shell function switches them."""
    home = tmp_path / "home"
    (home / ".codex-set").mkdir(parents=True)
    (home / ".codex-set" / "auth.json").write_text('{"a": "set"}', encoding="utf-8")
    (home / ".codex-csl").mkdir()
    (home / ".codex-csl" / "auth.json").write_text('{"a": "csl"}', encoding="utf-8")
    (home / ".codex").mkdir()
    try:
        (home / ".codex" / "auth.json").symlink_to(home / ".codex-set" / "auth.json")
    except OSError:
        (home / ".codex" / "auth.json").write_text('{"a": "set"}', encoding="utf-8")
    (home / ".codex-empty").mkdir()
    monkeypatch.setattr(keepalive_settings, "HOME", home)
    return home


def test_every_codex_account_is_found_once(homes: Path) -> None:
    """Accounts switch by CODEX_HOME in a shell function the schedule never loads.

    The scheduled call used the default home, which links to one account;
    the other was never woken. Each home holding credentials is an
    account, and two homes sharing the same credentials are one.
    """
    found = keepalive_runner.codex_homes()

    names = sorted(path.name for path in found)
    assert len(found) == 2
    assert ".codex-csl" in names
    assert ".codex-empty" not in names


def test_each_codex_account_gets_its_own_call(
    state: Path, homes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    seen = []

    def run(argv, **kwargs):
        seen.append((kwargs.get("env") or {}).get("CODEX_HOME"))
        return subprocess.CompletedProcess(argv, 0, "hi\n", "")

    monkeypatch.setattr(keepalive_runner.subprocess, "run", run)

    assert keepalive_runner.send("codex") == 0

    assert sorted(Path(home).name for home in seen) in (
        [".codex", ".codex-csl"], [".codex-csl", ".codex-set"],
    )
    logged = keepalive_settings.log_path("codex").read_text(encoding="utf-8")
    assert ".codex-csl" in logged


def test_one_account_failing_is_reported_but_the_others_still_run(
    state: Path, homes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    calls = []

    def run(argv, **kwargs):
        home = (kwargs.get("env") or {}).get("CODEX_HOME", "")
        calls.append(home)
        code = 1 if home.endswith("-csl") else 0
        return subprocess.CompletedProcess(argv, code, "hi\n", "ERROR: not logged in\n")

    monkeypatch.setattr(keepalive_runner.subprocess, "run", run)

    assert keepalive_runner.send("codex") == 1
    assert len(calls) == 2


def test_status_lists_the_last_result_of_each_account(state: Path) -> None:
    path = keepalive_settings.log_path("codex")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[t1] calling codex (.codex-set)\n[t1] exit 1: ERROR: usage limit\n"
        "[t1] calling codex (.codex-csl)\n[t1] exit 0: hi\n"
        "[t2] calling codex (.codex-set)\n[t2] exit 0: hi\n",
        encoding="utf-8",
    )

    latest = keepalive_window.last_by_account("codex")

    assert latest == {
        ".codex-set": "[t2] exit 0: hi",
        ".codex-csl": "[t1] exit 0: hi",
    }


def test_the_schedule_calls_the_launcher_that_updates_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabled from source, the timer ran `python -m ai_config`.

    That resolved to whatever that interpreter had installed — a stale
    1.0.75 copy in site-packages — so two releases of keepalive fixes
    never reached the schedule. The standalone launcher is the one path
    `acg update` keeps current.
    """
    from ai_config import paths

    launcher = tmp_path / "bin" / "ai-config"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(paths, "standalone_install_path", lambda: launcher)

    assert keepalive_scheduler._invocation("codex") == [str(launcher), "keepalive", "send", "codex"]


def test_without_a_launcher_the_schedule_uses_this_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from ai_config import paths

    monkeypatch.setattr(paths, "standalone_install_path", lambda: tmp_path / "none")
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert keepalive_scheduler._invocation()[:3] == [sys.executable, "-m", "ai_config"]


def _models_cache(home: Path, models: list) -> None:
    import json

    (home / "models_cache.json").write_text(json.dumps({"models": models}), encoding="utf-8")


def test_codex_picks_the_least_promoted_listed_model(tmp_path: Path) -> None:
    """A keepalive call exists to have happened; it should cost the least on offer.

    Nothing names a model: the choice comes from the account's own list,
    so a model that is retired or renamed is simply no longer picked.
    """
    _models_cache(tmp_path, [
        {"slug": "big", "priority": 1, "visibility": "list", "supported_in_api": True},
        {"slug": "small", "priority": 12, "visibility": "list", "supported_in_api": True},
        {"slug": "hidden", "priority": 40, "visibility": "hide", "supported_in_api": True},
    ])

    assert keepalive_runner.cheapest_codex_model(tmp_path) == "small"


def test_codex_without_a_cache_leaves_the_model_alone(tmp_path: Path) -> None:
    assert keepalive_runner.cheapest_codex_model(tmp_path) is None


def test_agy_wakes_each_quota_group_with_its_cheapest_model() -> None:
    """Only Flash was called; Gemini 4 and Claude/GPT kept unanchored windows (2026-10-30)."""
    listing = (
        "Fetching available models...\n"
        "gemini-4-argon-medium\tGemini 4 Argon (Medium)\n"
        "gemini-4-argon-low\tGemini 4 Argon (Low)\n"
        "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
        "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)\n"
        "claude-opus-5-5-low\tClaude Opus 5.5 (Low)\n"
        "claude-sonnet-5-5-low\tClaude Sonnet 5.5 (Low)\n"
        "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
    )

    assert keepalive_runner.agy_models_per_group(listing) == [
        ("gemini-flash", "gemini-3.6-flash-low"),
        ("gemini-4", "gemini-4-argon-low"),
        ("claude-gpt", "gpt-oss-120b-medium"),
    ]


def test_a_group_the_listing_lacks_is_skipped() -> None:
    listing = "claude-sonnet-5-5-low\tSonnet\n"

    assert keepalive_runner.agy_models_per_group(listing) == [("claude-gpt", "claude-sonnet-5-5-low")]


def test_agy_with_an_unreadable_listing_leaves_the_model_alone() -> None:
    assert keepalive_runner.agy_models_per_group("") == []


def test_each_codex_account_is_called_with_its_cheapest_model(
    state: Path, homes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    _models_cache(homes / ".codex-csl", [
        {"slug": "csl-small", "priority": 9, "visibility": "list", "supported_in_api": True},
    ])
    seen = {}

    def run(argv, **kwargs):
        seen[Path((kwargs.get("env") or {}).get("CODEX_HOME", "")).name] = list(argv)
        return subprocess.CompletedProcess(argv, 0, "hi\n", "")

    monkeypatch.setattr(keepalive_runner.subprocess, "run", run)

    keepalive_runner.send("codex")

    csl = seen[".codex-csl"]
    assert csl[csl.index("-m") + 1] == "csl-small"
    assert "-m" not in next(v for k, v in seen.items() if k != ".codex-csl")
    assert "csl-small" in keepalive_settings.log_path("codex").read_text(encoding="utf-8")


def test_keepalive_tasks_run_without_a_window(state: Path) -> None:
    """They fire during the day, where a terminal popping up is most visible."""
    for argv in keepalive_scheduler.schtasks_argv(("07:00",)):
        assert argv[argv.index("/TR") + 1].startswith("conhost.exe --headless cmd /c ")


def _agy(monkeypatch: pytest.MonkeyPatch, replies: list) -> list:
    """Fake agy: each call takes the next (exit code, conversation id) and is recorded."""
    import json
    import subprocess

    from ai_config import locking

    calls = []
    monkeypatch.setattr(keepalive_runner, "_agy_listing", lambda: "gemini-3.8-flash-low\tFlash\n")
    # 鎖檔放在暫存區,不碰這台真的 ~/.ai-config-backup
    monkeypatch.setattr(locking, "BACKUP_BASE", Path(keepalive_settings._state_dir()) / "locks")

    def run(argv, **kwargs):
        calls.append({"argv": list(argv), "cwd": kwargs.get("cwd")})
        code, conversation = replies.pop(0)
        # 只有帶了 --output-format json 才回 JSON:漏掉旗標時,ID 與回覆都讀不到
        wants_json = "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
        if code:
            stdout = ""
        elif wants_json:
            stdout = json.dumps({"conversation_id": conversation, "response": "hi\n"})
        else:
            stdout = "hi\n"
        return subprocess.CompletedProcess(argv, code, stdout, "" if code == 0 else "conversation not found")

    monkeypatch.setattr(keepalive_runner.subprocess, "run", run)
    return calls


def test_agy_keeps_one_conversation_instead_of_a_new_one_each_call(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A4000 had 123 "reply with only the word: hi" conversations in agy's resume list."""
    calls = _agy(monkeypatch, [(0, "first"), (0, "first")])

    assert keepalive_runner.send("agy") == 0
    assert keepalive_runner.send("agy") == 0

    assert "--conversation" not in calls[0]["argv"]
    second = calls[1]["argv"]
    assert second[second.index("--conversation") + 1] == "first"
    # 不掛在家目錄這個 workspace 底下
    assert all(call["cwd"] == state / "keepalive-agy" for call in calls)
    assert "exit 0: hi" in keepalive_settings.log_path("agy").read_text(encoding="utf-8")


def test_agy_starts_over_when_its_conversation_is_gone(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _agy(monkeypatch, [(0, "first"), (1, ""), (0, "second"), (0, "second")])

    keepalive_runner.send("agy")
    assert keepalive_runner.send("agy") == 0, "a lost conversation must not cost the wake-up"
    keepalive_runner.send("agy")

    assert "--conversation" not in calls[2]["argv"]
    assert calls[3]["argv"][calls[3]["argv"].index("--conversation") + 1] == "second"


def test_agy_opens_a_new_conversation_each_month(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    state.mkdir(parents=True, exist_ok=True)
    (state / "keepalive-agy-conversation.json").write_text(
        json.dumps({"id": "old", "month": "2000-01"}), encoding="utf-8"
    )
    calls = _agy(monkeypatch, [(0, "new"), (0, "new")])

    keepalive_runner.send("agy")
    keepalive_runner.send("agy")

    assert "--conversation" not in calls[0]["argv"]
    saved = json.loads((state / "keepalive-agy-conversation.json").read_text(encoding="utf-8"))
    assert saved["groups"]["gemini-flash"] == "new"
    assert saved["month"] != "2000-01", "the new month is recorded, or every call starts over"
    assert calls[1]["argv"][calls[1]["argv"].index("--conversation") + 1] == "new"


def test_agy_ignores_a_recorded_id_that_is_not_text(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-edited or corrupt record must not crash the scheduled wake-up."""
    import json
    from datetime import datetime

    state.mkdir(parents=True, exist_ok=True)
    month = datetime.now().astimezone().strftime("%Y-%m")
    (state / "keepalive-agy-conversation.json").write_text(
        json.dumps({"id": 42, "month": month}), encoding="utf-8"
    )
    calls = _agy(monkeypatch, [(0, "fresh")])

    assert keepalive_runner.send("agy") == 0
    assert "--conversation" not in calls[0]["argv"]


def test_a_second_agy_keepalive_waits_for_none(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both would read "no conversation yet" and each open one."""
    from ai_config import locking

    calls = _agy(monkeypatch, [])
    with locking.exclusive_lock(".acg-keepalive-agy.lock") as acquired:
        assert acquired
        assert keepalive_runner.send("agy") == 0

    assert calls == []
    assert "another agy keepalive is running" in keepalive_settings.log_path("agy").read_text(
        encoding="utf-8"
    )


def test_only_agy_replies_are_read_as_json(state: Path) -> None:
    """A claude or codex reply that happens to be JSON is logged as it came."""
    import subprocess

    done = subprocess.CompletedProcess([], 0, '{"response": "inner"}\n', "")

    assert keepalive_runner._outcome(done) == '{"response": "inner"}'
    assert keepalive_runner._outcome(done, json_reply=True) == "inner"


def test_each_agy_group_keeps_its_own_conversation(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _agy(monkeypatch, [(0, "f"), (0, "g"), (0, "c"), (0, "f"), (1, ""), (0, "c2"), (0, "c2")])
    monkeypatch.setattr(keepalive_runner, "_agy_listing", lambda: (
        "gemini-3.8-flash-low\tFlash\ngemini-4-argon-low\tArgon\ngpt-oss-120b-medium\tGPT\n"
    ))

    keepalive_runner.send("agy")
    # 第二輪:Gemini 4 那組的對話不見了,其他組照樣接著原本的對話
    keepalive_runner.send("agy")

    models = [call["argv"][call["argv"].index("--model") + 1] for call in calls]
    assert models[:3] == ["gemini-3.8-flash-low", "gemini-4-argon-low", "gpt-oss-120b-medium"]
    resumed = [
        call["argv"][call["argv"].index("--conversation") + 1]
        for call in calls[3:] if "--conversation" in call["argv"]
    ]
    assert resumed == ["f", "g", "c"]


def test_agy_groups_do_not_overlap_and_prefer_the_cheaper_family() -> None:
    listing = (
        "gemini-4-flash-low\tGemini 4 Flash\n"
        "gemini-4-argon-low\tGemini 4 Argon\n"
        "gemini-3.8-flash-low\tFlash\n"
        "gpt-5-low\tGPT 5\n"
        "gpt-oss-120b-medium\tGPT-OSS\n"
        "claude-opus-5-5-low\tOpus\n"
        "claude-sonnet-5-5-low\tSonnet\n"
    )

    picks = dict(keepalive_runner.agy_models_per_group(listing))

    assert picks["gemini-flash"] == "gemini-3.8-flash-low"
    assert picks["gemini-4"].startswith("gemini-4")
    assert picks["claude-gpt"] == "gpt-oss-120b-medium"
    assert dict(keepalive_runner.agy_models_per_group(
        "claude-opus-5-5-low\tOpus\nclaude-sonnet-5-5-low\tSonnet\n"
    ))["claude-gpt"] == "claude-sonnet-5-5-low"


def test_a_signal_killed_group_counts_as_a_failure(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _agy(monkeypatch, [(-9, ""), (-9, "")])

    assert keepalive_runner.send("agy") != 0
    assert "no recognised model" not in keepalive_settings.log_path("agy").read_text(encoding="utf-8")
