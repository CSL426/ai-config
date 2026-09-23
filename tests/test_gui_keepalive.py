"""GUI keepalive calls preserve core outcomes and isolate scheduler failures."""

from unittest.mock import Mock

import pytest

from ai_config import keepalive
from ai_config.commands.gui import GuiApi
from ai_config.gui_management import _keepalive_state


@pytest.fixture
def core(monkeypatch):
    # Mock every scheduler entry point so these bridge tests cannot touch it.
    methods = {
        "enable": Mock(return_value=(0, ["enabled", "07:00"])),
        "disable": Mock(return_value=(0, ["disabled"])),
        "load": Mock(return_value=keepalive.Settings(
            times=("07:00", "12:05"), model="test-model",
        )),
        "installed": Mock(return_value=True),
        "existing_ccs": Mock(return_value="ccs-test"),
        "last_runs": Mock(return_value=["completed"]),
        "last_by_account": Mock(return_value={}),
        "current_window": Mock(return_value=None),
    }
    for name, method in methods.items():
        monkeypatch.setattr(keepalive, name, method)
    return methods


@pytest.mark.parametrize("times", [None, [], ["07:00", "12:05"]])
def test_enable_forwards_times_while_holding_lock(core, times):
    api = GuiApi()

    def enable(chosen, tool=None):
        assert api._lock.locked()
        return 0, ["enabled", "schedule ready"]

    core["enable"].side_effect = enable
    result = api.set_keepalive(True, times)

    core["enable"].assert_called_once_with(tuple(times or ()), tool="claude")
    core["disable"].assert_not_called()
    assert result["code"] == 0
    assert result["error"] is None
    assert result["output"] == "enabled\nschedule ready"
    assert not api._lock.locked()


def test_disable_forwards_without_times_while_holding_lock(core):
    api = GuiApi()

    def disable(tool=None):
        assert api._lock.locked()
        return 0, ["disabled"]

    core["disable"].side_effect = disable
    result = api.set_keepalive(False, ["07:00"])

    core["disable"].assert_called_once_with("claude")
    core["enable"].assert_not_called()
    assert result["code"] == 0
    assert result["error"] is None
    assert result["output"] == "disabled"
    assert not api._lock.locked()


@pytest.mark.parametrize("wanted", [None, "true", 0, 1, [], {}])
def test_invalid_wanted_never_calls_core(core, wanted):
    api = GuiApi()

    result = api.set_keepalive(wanted)

    assert result["code"] == 1
    assert result["error"] == "INVALID_ARGUMENT"
    core["enable"].assert_not_called()
    core["disable"].assert_not_called()
    assert not api._lock.locked()


@pytest.mark.parametrize("wanted", [True, False])
def test_busy_never_calls_core_or_releases_another_actions_lock(core, wanted):
    api = GuiApi()

    with api._lock:
        result = api.set_keepalive(wanted)
        assert api._lock.locked()

    assert result["code"] == 1
    assert result["error"] == "BUSY"
    core["enable"].assert_not_called()
    core["disable"].assert_not_called()


def test_ccs_refusal_preserves_core_message_and_releases_lock(core):
    api = GuiApi()
    core["enable"].return_value = (1, ["ccs already schedules calls", "refused"])

    result = api.set_keepalive(True, ["07:00"])

    assert result["code"] == 1
    assert result["error"] == "KEEPALIVE_REFUSED"
    assert result["output"] == "ccs already schedules calls\nrefused"
    core["enable"].assert_called_once_with(("07:00",), tool="claude")
    core["disable"].assert_not_called()
    assert not api._lock.locked()


@pytest.mark.parametrize("wanted, method", [(True, "enable"), (False, "disable")])
@pytest.mark.parametrize("exception, error", [
    (OSError("scheduler unavailable"), "IO_ERROR"),
    (RuntimeError("scheduler failed"), "CONFLICT"),
    (ValueError("invalid time"), "INVALID_ARGUMENT"),
    (TimeoutError("scheduler busy"), "BUSY"),
])
def test_core_failure_returns_error_and_releases_lock(
    core, wanted, method, exception, error,
):
    api = GuiApi()
    core[method].side_effect = exception

    result = api.set_keepalive(wanted)

    assert result["code"] == 1
    assert result["error"] == error
    assert result["output"] == str(exception)
    assert not api._lock.locked()

    core[method].side_effect = None
    assert api.set_keepalive(wanted)["code"] == 0


def test_state_exposes_settings_scheduler_and_recent_runs(core):
    state = _keepalive_state()

    assert state["installed"] is True
    assert state["times"] == ["07:00", "12:05"]
    assert state["model"] == "test-model"
    assert state["ccs"] == "ccs-test"
    assert state["recent"] == ["completed"]
    # 每個工具各有自己的視窗,所以三個都要問一遍
    assert sorted(state["tools"]) == ["agy", "claude", "codex"]
    core["existing_ccs"].assert_called_once_with()


@pytest.mark.parametrize("method", [
    "load", "installed", "existing_ccs", "last_runs",
])
@pytest.mark.parametrize("exception", [
    ImportError, OSError, RuntimeError, ValueError,
])
def test_state_read_failure_returns_safe_fallback(core, method, exception):
    core[method].side_effect = exception("state unavailable")

    assert _keepalive_state() == {
        "installed": False,
        "times": [],
        "model": "",
        "ccs": "",
        "recent": [],
        "tools": {},
        "window": None,
    }


def test_the_page_gets_the_window_and_each_account(core, monkeypatch):
    """The CLI showed the real window and every codex account; the page did not.

    Features landed in the CLI and the page was judged not to need them,
    so the one place most people look still showed a single log line.
    """
    from datetime import datetime

    start = datetime(2026, 9, 23, 9, 50).astimezone()
    reset = datetime(2026, 9, 23, 14, 50).astimezone()
    monkeypatch.setattr(keepalive, "current_window", lambda now=None: (start, reset))
    monkeypatch.setattr(keepalive, "drift", lambda s, times, now: "07:00")
    monkeypatch.setattr(
        keepalive, "last_by_account",
        lambda tool="claude": {".codex-set": "exit 0: hi"} if tool == "codex" else {},
    )

    state = _keepalive_state()

    assert state["window"] == {"start": "09:50", "reset": "14:50", "drift": "07:00"}
    assert state["tools"]["codex"]["accounts"] == {".codex-set": "exit 0: hi"}


def test_no_recorded_window_is_none(core, monkeypatch):
    monkeypatch.setattr(keepalive, "current_window", lambda now=None: None)
    monkeypatch.setattr(keepalive, "last_by_account", lambda tool="claude": {})

    assert _keepalive_state()["window"] is None
