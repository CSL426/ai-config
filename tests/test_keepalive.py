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
    assert "--effort" in agy and "low" in agy


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
