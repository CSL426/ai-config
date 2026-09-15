"""GUI reminder writes use the same core API and action lock as the CLI."""

import pytest

from ai_config import handoff_reminder
from ai_config.commands.gui import GuiApi
from ai_config.gui_management import _handoff_reminder_state


@pytest.mark.parametrize("enabled, threshold", [
    ("true", 70), (True, True), (False, "70"), (True, 0),
    (True, 100), (True, 70.5), (None, 70),
])
def test_reminder_rejects_invalid_arguments(enabled, threshold):
    assert GuiApi().set_handoff_reminder(
        enabled, threshold
    )["error"] == "INVALID_ARGUMENT"


def test_reminder_busy_does_not_discard_preview():
    api = GuiApi()
    api._push_preview = ("memory", "token", "fingerprint")
    with api._lock:
        assert api.set_handoff_reminder(True)["error"] == "BUSY"
    assert api._push_preview is not None


@pytest.mark.parametrize("enabled", [True, False])
def test_reminder_configure_discards_preview_and_holds_gui_lock(
    monkeypatch, enabled,
):
    api = GuiApi()
    monkeypatch.setattr(api, "_ensure_configured", lambda: None)
    api._push_preview = ("memory", "token", "fingerprint")

    def configure(wanted, threshold):
        assert api._lock.locked()
        assert api._push_preview is None
        assert (wanted, threshold) == (enabled, 80)
        return {"enabled": wanted, "threshold": threshold, "installed": wanted}

    monkeypatch.setattr(handoff_reminder, "configure", configure)
    result = api.set_handoff_reminder(enabled, 80)
    assert result["code"] == 0
    assert result["handoff_reminder"]["enabled"] is enabled
    assert not api._lock.locked()


def test_reminder_core_lock_failure_releases_gui_lock(monkeypatch):
    api = GuiApi()
    monkeypatch.setattr(api, "_ensure_configured", lambda: None)

    def busy(*args):
        raise TimeoutError("apply lock busy")

    monkeypatch.setattr(handoff_reminder, "configure", busy)
    assert api.set_handoff_reminder(True)["error"] == "BUSY"
    assert not api._lock.locked()


def test_reminder_status_failure_does_not_break_memory_page(monkeypatch):
    def broken():
        raise ValueError("settings 格式錯誤")

    monkeypatch.setattr(handoff_reminder, "status", broken)
    state = _handoff_reminder_state()
    assert state["reason"] == "settings 格式錯誤"
    assert state["enabled"] is False
