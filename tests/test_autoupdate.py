"""Daily update of acg and the AI CLIs: every tool asked, failures remembered."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from ai_config import autoupdate, daily_job, locking, memory_hooks, paths
from ai_config.commands.autoupdate import run_autoupdate
from ai_config.gui_api import GuiApi


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(daily_job, "platform_name", lambda: "linux")
    monkeypatch.setattr(paths, "scheduled_command", lambda: ["acg"])
    monkeypatch.setattr(locking, "BACKUP_BASE", tmp_path / "backup")
    return tmp_path


class FakeTools:
    """Each tool reports a version and moves to the next one when told to update."""

    def __init__(self, versions: dict, failing: "dict | None" = None) -> None:
        self.versions = dict(versions)
        self.failing = failing or {}
        self.calls: list = []

    def binary(self, tool: str) -> "str | None":
        return f"/opt/{tool}/bin/{tool}" if tool in self.versions else None

    def run(self, argv: list, timeout: float = 0) -> "tuple[int, str]":
        name = Path(argv[0]).name
        self.calls.append([name, *argv[1:]])
        if argv[-1] == "--version":
            return 0, f"{name} {self.versions[name][0]}\n"
        if name in self.failing:
            return 1, self.failing[name]
        self.versions[name] = self.versions[name][1:] or self.versions[name]
        return 0, "done\n"


@pytest.fixture
def tools(state: Path, monkeypatch: pytest.MonkeyPatch):
    def install(versions: dict, failing: "dict | None" = None) -> FakeTools:
        fake = FakeTools(versions, failing)
        monkeypatch.setattr(autoupdate, "_binary", fake.binary)
        monkeypatch.setattr(autoupdate, "_run", fake.run)
        return fake
    return install


def test_every_installed_tool_is_updated_and_acg_goes_last(tools) -> None:
    fake = tools({
        "claude": ["2.1.0", "2.1.1"], "agy": ["1.2.13", "1.2.14"],
        "acg": ["1.0.101"],
    })

    assert autoupdate.run() == 0

    updates = [call[0] for call in fake.calls if call[-1] == "update"]
    # 沒裝 codex 就不碰它;acg 最後,因為它更新 plugin 時要用到新的 claude
    assert updates == ["claude", "agy", "acg"]
    steps = autoupdate.last_run()["steps"]
    assert [(s.name, s.before, s.after, s.ok) for s in steps] == [
        ("claude", "2.1.0", "2.1.1", True),
        ("agy", "1.2.13", "1.2.14", True),
        ("acg", "1.0.101", "1.0.101", True),
    ]
    assert steps[0].line() == "✓ claude:2.1.0 → 2.1.1"
    assert steps[2].line() == "✓ acg:1.0.101 已是最新"


def test_one_failure_does_not_stop_the_rest_and_says_why(tools) -> None:
    fake = tools(
        {"claude": ["2.1.0"], "codex": ["0.159.2", "0.160.0"], "acg": ["1.0.101"]},
        failing={"claude": "Checking for updates\nError: permission denied writing lock\nbye\n"},
    )

    assert autoupdate.run() == 1

    assert [c[0] for c in fake.calls if c[-1] == "update"] == ["claude", "codex", "acg"]
    when, failed = autoupdate.failures()
    assert when
    assert [(s.name, s.note) for s in failed] == [
        ("claude", "Error: permission denied writing lock"),
    ]


def test_a_clean_run_clears_the_failure(tools, monkeypatch: pytest.MonkeyPatch) -> None:
    tools({"claude": ["2.1.0"], "acg": ["1.0.101"]}, failing={"claude": "error: offline"})
    autoupdate.run()
    assert autoupdate.failures()[1]

    tools({"claude": ["2.1.0"], "acg": ["1.0.101"]})
    autoupdate.run()

    assert autoupdate.failures()[1] == []


def test_an_npm_codex_is_reported_not_updated(tools, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tools({"codex": ["0.77.0"], "acg": ["1.0.101"]})
    monkeypatch.setattr(
        autoupdate, "_binary",
        lambda tool: "/usr/lib/node_modules/@openai/codex/bin/codex" if tool == "codex" else None,
    )

    assert autoupdate.run() == 0

    assert ["codex", "update"] not in fake.calls
    step = autoupdate.last_run()["steps"][0]
    assert step.name == "codex" and step.ok and "npm" in step.note


@pytest.mark.parametrize("binary", [
    "/usr/lib/node_modules/@openai/codex/bin/codex.js",
    "C:/Users/me/AppData/Roaming/npm/codex.cmd",
])
def test_both_npm_layouts_are_recognized(binary: str) -> None:
    assert autoupdate._is_npm_install(binary)


def test_the_standalone_codex_is_not_mistaken_for_npm() -> None:
    assert not autoupdate._is_npm_install("/home/me/.codex/packages/standalone/releases/0.159.2/bin/codex")


def test_executables_renamed_aside_are_removed(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "agy.exe"
    launcher.write_bytes(b"new")
    (bin_dir / "agy.exe.1790655676061196200.old").write_bytes(b"x" * 2048)
    (bin_dir / "agy.exe.old.1790655676").write_bytes(b"x" * 1024)
    (bin_dir / "claude.exe.old.123").write_bytes(b"other tool")
    (bin_dir / "agy.exe.settings").write_bytes(b"not a leftover")

    assert autoupdate._remove_replaced(str(launcher)) == 3072

    assert sorted(p.name for p in bin_dir.iterdir()) == [
        "agy.exe", "agy.exe.settings", "claude.exe.old.123",
    ]


def test_the_space_freed_is_reported_and_remembered(tools, monkeypatch: pytest.MonkeyPatch) -> None:
    tools({"agy": ["1.2.13", "1.2.14"], "acg": ["1.0.101"]})
    monkeypatch.setattr(autoupdate, "_remove_replaced", lambda binary: 200 * 2**20)

    autoupdate.run()

    step = autoupdate.last_run()["steps"][0]
    assert step.freed == 200 * 2**20
    assert step.line() == "✓ agy:1.2.13 → 1.2.14(清掉舊執行檔 200 MB)"


def test_a_tool_that_hangs_is_a_failure_not_a_stall(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(*args, **kwargs):
        raise autoupdate.subprocess.TimeoutExpired(args[0], 600)

    monkeypatch.setattr(autoupdate.subprocess, "run", hang)

    code, output = autoupdate._run(["claude", "update"])

    assert code != 0 and "600" in output


def test_the_timer_runs_autoupdate_daily_and_catches_up(state: Path) -> None:
    service, timer = autoupdate.job().systemd_units(5, 30)

    assert "OnCalendar=*-*-* 05:30:00" in timer
    assert "Persistent=true" in timer
    assert "ExecStart=acg autoupdate run" in service
    # 四個工具各自下載,十五分鐘不夠
    assert "RuntimeMaxSec=3600" in service


def test_windows_runs_through_cmd_at_the_chosen_time(state: Path) -> None:
    argv = autoupdate.job().schtasks_argv(5, 30)

    assert argv[argv.index("/ST") + 1] == "05:30"
    assert argv[argv.index("/TR") + 1] == "cmd /c acg autoupdate run"


def test_launchd_runs_the_same_command(state: Path) -> None:
    import plistlib

    parsed = plistlib.loads(autoupdate.job().launchd_plist(5, 30))

    assert parsed["ProgramArguments"] == ["acg", "autoupdate", "run"]
    assert parsed["StartCalendarInterval"] == {"Hour": 5, "Minute": 30}


@pytest.mark.parametrize("clock", ["25:00", "noon", "07:61"])
def test_an_unreadable_time_is_refused(state: Path, clock: str) -> None:
    with pytest.raises(ValueError):
        autoupdate.parse_clock(clock)


def test_enable_writes_the_timer_and_status_reads_the_time_back(
    state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    systemctl = Mock(return_value=Mock(returncode=0, stderr=""))
    monkeypatch.setattr(daily_job, "_systemctl", systemctl)
    monkeypatch.setattr(daily_job, "forget_missed_runs", lambda timer: None)

    autoupdate.enable("06:40")

    assert autoupdate.status()["installed"] is True
    assert autoupdate.status()["time"] == "06:40"
    systemctl.assert_any_call("enable", "--now", "acg-autoupdate.timer")

    autoupdate.disable()

    assert autoupdate.status()["installed"] is False


def test_a_timer_systemd_did_not_load_is_not_reported_as_enabled(
    state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daily_job, "_systemctl", Mock(return_value=Mock(returncode=1, stderr="no bus")))
    monkeypatch.setattr(daily_job, "forget_missed_runs", lambda timer: None)

    with pytest.raises(RuntimeError, match="no bus"):
        autoupdate.enable()

    assert autoupdate.status()["installed"] is False


def test_a_timer_that_would_not_stop_keeps_its_files(
    state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    systemctl = Mock(return_value=Mock(returncode=0, stderr=""))
    monkeypatch.setattr(daily_job, "_systemctl", systemctl)
    monkeypatch.setattr(daily_job, "forget_missed_runs", lambda timer: None)
    autoupdate.enable()
    systemctl.return_value = Mock(returncode=1, stderr="Failed to connect to bus")

    with pytest.raises(RuntimeError, match="bus"):
        autoupdate.disable()

    assert autoupdate.status()["installed"] is True


def test_a_second_run_while_one_is_going_does_nothing(
    tools, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    import contextlib

    fake = tools({"claude": ["2.1.0"], "acg": ["1.0.101"]})
    monkeypatch.setattr(
        locking, "exclusive_lock", lambda name: contextlib.nullcontext(False),
    )

    assert autoupdate.run() == 1

    assert fake.calls == []
    assert autoupdate.last_run() is None
    assert "正在進行" in capsys.readouterr().out


def test_status_lists_the_last_run(tools, capsys: pytest.CaptureFixture) -> None:
    tools({"claude": ["2.1.0"], "acg": ["1.0.101"]}, failing={"claude": "error: offline"})
    autoupdate.run()
    capsys.readouterr()

    assert run_autoupdate(["status"]) == 0

    output = capsys.readouterr().out
    assert "未啟用" in output
    assert "✗ claude:error: offline" in output
    assert "autoupdate run" in output


def test_an_unknown_action_is_refused(state: Path) -> None:
    assert run_autoupdate(["enable", "05:30", "extra"]) == 1
    assert run_autoupdate(["now"]) == 1


def test_a_new_session_hears_about_a_failed_update(
    tools, capsys: pytest.CaptureFixture,
) -> None:
    tools({"agy": ["1.2.13"], "acg": ["1.0.101"]}, failing={"agy": "Update failed: network"})
    autoupdate.run()
    capsys.readouterr()

    memory_hooks._announce_failed_autoupdate({"hook_event_name": "SessionStart"})
    memory_hooks._announce_failed_autoupdate({"hook_event_name": "UserPromptSubmit"})

    output = capsys.readouterr().out
    assert output.count("自動更新") == 1
    assert "agy:Update failed: network" in output


def test_the_desktop_app_toggles_and_moves_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    enable = Mock(return_value=["已排定每天 06:00"])
    disable = Mock(return_value=["移除"])
    monkeypatch.setattr(autoupdate, "enable", enable)
    monkeypatch.setattr(autoupdate, "disable", disable)
    api = GuiApi()

    assert api.set_autoupdate(True, "06:00")["output"] == "已排定每天 06:00"
    assert api.set_autoupdate(False)["code"] == 0
    assert api.set_autoupdate("yes")["error"] == "INVALID_ARGUMENT"

    enable.assert_called_once_with("06:00")
    disable.assert_called_once_with()
    assert not api._lock.locked()
