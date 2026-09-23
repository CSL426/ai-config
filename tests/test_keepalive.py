"""Anchoring the usage window: chosen times, one throwaway call each."""

from pathlib import Path

import pytest

from ai_config import keepalive


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path / "acg"


def test_times_are_ordered_and_deduplicated() -> None:
    parsed = keepalive.parse_times(["22:15", "07:00", "22:15", "12:05"])

    assert parsed.times == ["07:00", "12:05", "22:15"]
    assert parsed.rejected == []


def test_an_unusable_time_is_reported_not_dropped() -> None:
    """Scheduling three of the four times somebody asked for is worse than refusing."""
    parsed = keepalive.parse_times(["07:00", "25:00", "noon", "12:5"])

    assert parsed.times == ["07:00"]
    assert parsed.rejected == ["25:00", "noon", "12:5"]


def test_settings_survive_a_round_trip(state: Path) -> None:
    keepalive.save(keepalive.Settings(times=("06:30", "11:35"), model="m"))

    loaded = keepalive.load()

    assert loaded.times == ("06:30", "11:35")
    assert loaded.model == "m"


def test_a_broken_config_falls_back_to_defaults(state: Path) -> None:
    """A machine with an unreadable file should still anchor its window."""
    path = keepalive.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert keepalive.load().times == keepalive.DEFAULT_TIMES


def test_the_call_names_the_model_and_prompt(state: Path) -> None:
    keepalive.save(keepalive.Settings(model="haiku", prompt="hi", claude_path="/c"))

    assert keepalive.run_args() == ["/c", "--model", "haiku", "-p", "hi"]


def test_a_failed_call_is_logged_and_does_not_raise(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing one costs a later window, which is not worth failing over."""
    def explode(*args, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(keepalive.subprocess, "run", explode)

    assert keepalive.send() == 1
    assert "failed to start" in keepalive.log_path().read_text(encoding="utf-8")


def test_the_schedule_covers_every_chosen_time(state: Path) -> None:
    service, timer = keepalive.systemd_units(("07:00", "12:05"))

    assert "OnCalendar=*-*-* 07:00:00" in timer
    assert "OnCalendar=*-*-* 12:05:00" in timer
    # 機器睡過那個時間就整天錯位,而且日誌少一筆沒人會去數
    assert "Persistent=true" in timer
    assert "keepalive" in service


def test_windows_gets_one_task_per_time(state: Path) -> None:
    """schtasks has no repeating-daily-times form, so each time is its own task."""
    argv = keepalive.schtasks_argv(("07:00", "22:15"))

    names = [a[a.index("/TN") + 1] for a in argv]
    assert names == ["acg keepalive 0700", "acg keepalive 2215"]
    assert all("/ST" in a for a in argv)


def test_launchd_lists_every_time(state: Path) -> None:
    import plistlib

    plist = plistlib.loads(keepalive.launchd_plist(("07:00", "12:05")))

    assert plist["StartCalendarInterval"] == [
        {"Hour": 7, "Minute": 0}, {"Hour": 12, "Minute": 5},
    ]


def test_enable_refuses_while_claude_scheduler_is_installed(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both anchor the same window, so leaving the old one doubles every call."""
    monkeypatch.setattr(keepalive, "existing_ccs", lambda: "crontab(...)")

    code, lines = keepalive.enable(("07:00",))

    assert code == 1
    assert any("claude-scheduler" in line for line in lines)
    assert any("一天燒兩倍" in line for line in lines)
    # 拒絕就不該留下半套:設定不能被寫進去
    assert not keepalive.config_path().is_file()


def test_enable_rejects_a_time_it_cannot_read(state: Path) -> None:
    code, lines = keepalive.enable(("07:00", "half past nine"))

    assert code == 1
    assert any("half past nine" in line for line in lines)


def test_too_many_times_are_refused(state: Path) -> None:
    code, _ = keepalive.enable(tuple(f"{h:02d}:00" for h in range(9)))

    assert code == 1


def test_each_tool_keeps_its_own_times(state: Path) -> None:
    """Three windows with no reason to share a boundary.

    Tying them to one list would move all three whenever one needed a
    different hour.
    """
    keepalive.save(keepalive.Settings(times=("07:00",)), tool="claude")
    keepalive.save(keepalive.Settings(times=("08:30",)), tool="codex")

    assert keepalive.load("claude").times == ("07:00",)
    assert keepalive.load("codex").times == ("08:30",)
    assert keepalive.load("agy").times == keepalive.DEFAULT_TIMES


def test_every_tool_calls_its_own_binary_the_cheap_way(state: Path) -> None:
    """Weakest model, least thinking: the call exists to have happened."""
    claude = keepalive.run_args(tool="claude")
    codex = keepalive.run_args(tool="codex")
    agy = keepalive.run_args(tool="agy")

    assert "--model" in claude and "-p" in claude
    assert "exec" in codex and "model_reasoning_effort=" in " ".join(codex)
    # agy 不帶 effort 旗標:見 test_agy_does_not_pass_an_unsupported_flag
    assert "-p" in agy and agy[0].endswith("agy")


def test_an_unknown_tool_is_refused(state: Path) -> None:
    with pytest.raises(ValueError, match="不認得"):
        keepalive.run_args(tool="gemini")


def test_enabling_one_tool_leaves_the_others_alone(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each tool has its own schedule; enabling codex must not disturb claude."""
    monkeypatch.setattr(keepalive, "existing_ccs", lambda: "")
    monkeypatch.setattr(keepalive, "_enable_systemd", lambda times, tool: ["ok"])
    monkeypatch.setattr(keepalive, "platform_name", lambda: "linux")

    keepalive.enable(("07:00",), tool="claude")
    keepalive.enable(("09:00",), tool="codex")

    assert keepalive.load("claude").times == ("07:00",)
    assert keepalive.load("codex").times == ("09:00",)


def test_codex_asks_for_an_effort_the_api_accepts(state: Path) -> None:
    """minimal reads like the cheapest choice and the API rejects it.

    Three scheduled calls failed with "'minimal' is not supported with
    the 'gpt-6-astra' model. Supported values are: 'low', 'medium',
    'high', 'xhigh', and 'max'." The log said only "exit 1: (no output)",
    so nothing pointed at the flag.
    """
    joined = " ".join(keepalive.run_args(tool="codex"))

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
    assert "--effort" not in keepalive.run_args(tool="agy")


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
    monkeypatch.setattr(keepalive.subprocess, "run", _finished(
        1, stderr="hook: SessionStart\nERROR: You've hit your usage limit. "
        "try again at 12:45 PM.\n",
    ))

    assert keepalive.send() == 1

    logged = keepalive.log_path().read_text(encoding="utf-8")
    assert "usage limit" in logged
    assert "12:45" in logged


def test_the_error_line_wins_over_trailing_noise(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools print banners and hook chatter after the error; the log needs the error."""
    monkeypatch.setattr(keepalive.subprocess, "run", _finished(
        1, stderr="ERROR: model not found\nhook: Stop\nhook: Stop Completed\n",
    ))

    keepalive.send()

    assert "model not found" in keepalive.log_path().read_text(encoding="utf-8")


def test_a_success_still_logs_the_reply(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keepalive.subprocess, "run", _finished(
        0, stdout="hi\n", stderr="some warning about config\n",
    ))

    keepalive.send()

    last = keepalive.log_path().read_text(encoding="utf-8").strip().splitlines()[-1]
    assert last.endswith("exit 0: hi")


def test_the_window_claude_reports_is_remembered(state: Path) -> None:
    """The status line is the only place the real reset time shows up.

    A reset four hours off the schedule could only be spotted by reading
    it off the screen; nothing kept it, so nothing could compare it.
    """
    from datetime import datetime

    reset = datetime(2026, 9, 23, 14, 50).astimezone()
    keepalive.record_window({"rate_limits": {"five_hour": {"resets_at": int(reset.timestamp())}}})

    start, end = keepalive.current_window(now=datetime(2026, 9, 23, 10, 27).astimezone())
    assert (start.hour, start.minute) == (9, 50)
    assert (end.hour, end.minute) == (14, 50)


def test_an_iso_reset_time_is_read_too(state: Path) -> None:
    keepalive.record_window(
        {"rate_limits": {"five_hour": {"resets_at": "2026-09-23T06:50:00Z"}}}
    )

    window = keepalive.current_window(
        now=datetime_utc(2026, 9, 23, 2, 27)
    )
    assert window is not None
    assert window[1] == datetime_utc(2026, 9, 23, 6, 50)


def test_a_payload_without_limits_records_nothing(state: Path) -> None:
    keepalive.record_window({"model": {"id": "x"}})

    assert keepalive.current_window() is None


def test_a_window_off_the_schedule_is_called_out(state: Path) -> None:
    """07:00 was the anchor; a window starting 09:50 means the call anchored nothing."""
    from datetime import datetime

    now = datetime(2026, 9, 23, 10, 27).astimezone()
    start = datetime(2026, 9, 23, 9, 50).astimezone()

    assert keepalive.drift(start, ("07:00", "12:05"), now) == "07:00"


def test_a_window_on_the_schedule_is_not(state: Path) -> None:
    from datetime import datetime

    now = datetime(2026, 9, 23, 10, 27).astimezone()
    start = datetime(2026, 9, 23, 7, 0).astimezone()

    assert keepalive.drift(start, ("07:00", "12:05"), now) == ""


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
    keepalive.save(keepalive.Settings(times=(anchor.strftime("%H:%M"),)))
    late = now + timedelta(hours=3)
    keepalive.record_window({"rate_limits": {"five_hour": {"resets_at": int(late.timestamp())}}})
    monkeypatch.setattr(keepalive, "installed", lambda tool="claude": True)

    command._report("claude")

    out = capsys.readouterr().out
    assert f"目前視窗 {(late - keepalive.WINDOW):%H:%M}–{late:%H:%M}" in out
    assert f"不是從排程的 {anchor:%H:%M} 開始" in out


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
    monkeypatch.setattr(keepalive, "HOME", home)
    return home


def test_every_codex_account_is_found_once(homes: Path) -> None:
    """Accounts switch by CODEX_HOME in a shell function the schedule never loads.

    The scheduled call used the default home, which links to one account;
    the other was never woken. Each home holding credentials is an
    account, and two homes sharing the same credentials are one.
    """
    found = keepalive.codex_homes()

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

    monkeypatch.setattr(keepalive.subprocess, "run", run)

    assert keepalive.send("codex") == 0

    assert sorted(Path(home).name for home in seen) in (
        [".codex", ".codex-csl"], [".codex-csl", ".codex-set"],
    )
    logged = keepalive.log_path("codex").read_text(encoding="utf-8")
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
        return subprocess.CompletedProcess(argv, code, "hi\n", "ERROR: usage limit\n")

    monkeypatch.setattr(keepalive.subprocess, "run", run)

    assert keepalive.send("codex") == 1
    assert len(calls) == 2


def test_status_lists_the_last_result_of_each_account(state: Path) -> None:
    path = keepalive.log_path("codex")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[t1] calling codex (.codex-set)\n[t1] exit 1: ERROR: usage limit\n"
        "[t1] calling codex (.codex-csl)\n[t1] exit 0: hi\n"
        "[t2] calling codex (.codex-set)\n[t2] exit 0: hi\n",
        encoding="utf-8",
    )

    latest = keepalive.last_by_account("codex")

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

    assert keepalive._invocation("codex") == [str(launcher), "keepalive", "send", "codex"]


def test_without_a_launcher_the_schedule_uses_this_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from ai_config import paths

    monkeypatch.setattr(paths, "standalone_install_path", lambda: tmp_path / "none")
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert keepalive._invocation()[:3] == [sys.executable, "-m", "ai_config"]


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

    assert keepalive.cheapest_codex_model(tmp_path) == "small"


def test_codex_without_a_cache_leaves_the_model_alone(tmp_path: Path) -> None:
    assert keepalive.cheapest_codex_model(tmp_path) is None


def test_agy_picks_the_oldest_low_flash() -> None:
    listing = (
        "Fetching available models...\n"
        "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
        "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)\n"
        "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\n"
        "claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n"
    )

    assert keepalive.cheapest_agy_model(listing) == "gemini-3.6-flash-low"


def test_agy_with_an_unreadable_listing_leaves_the_model_alone() -> None:
    assert keepalive.cheapest_agy_model("") is None


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

    monkeypatch.setattr(keepalive.subprocess, "run", run)

    keepalive.send("codex")

    csl = seen[".codex-csl"]
    assert csl[csl.index("-m") + 1] == "csl-small"
    assert "-m" not in next(v for k, v in seen.items() if k != ".codex-csl")
    assert "csl-small" in keepalive.log_path("codex").read_text(encoding="utf-8")
