"""What the log says about the usage window: when it started, how far it drifted."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import keepalive_settings


def last_runs(limit: int = 3, tool: str = keepalive_settings.DEFAULT_TOOL) -> list:
    try:
        lines = keepalive_settings.log_path(tool).read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return []
    return lines[-limit:]


WINDOW = timedelta(hours=5)
# 錨點跟實際起點差這麼多以內都算準:視窗起點可能被取整到整點
_DRIFT_TOLERANCE = timedelta(minutes=10)


def window_path() -> Path:
    return keepalive_settings._state_dir() / "window-claude.json"


def _parse_reset(value: object) -> "datetime | None":
    # resets_at 有時是 unix 秒數,有時是 ISO 8601
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def record_window(payload: dict) -> None:
    """Keep the reset time Claude Code reports, so it can be compared later.

    The status line is the only place the real window shows up; without
    keeping it, a reset hours off the schedule could only be read off
    the screen. Written only when it changes: this runs on every redraw.
    """
    limits = payload.get("rate_limits")
    five = limits.get("five_hour") if isinstance(limits, dict) else None
    reset = _parse_reset(five.get("resets_at")) if isinstance(five, dict) else None
    if reset is None:
        return
    stamp = int(reset.timestamp())
    path = window_path()
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("resets_at") == stamp:
            return
    except (OSError, ValueError, AttributeError):
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"resets_at": stamp}) + "\n", encoding="utf-8")
    except OSError:
        pass


def current_window(now: "datetime | None" = None) -> "tuple | None":
    """(start, reset) of the window last reported, if it is still running."""
    try:
        stamp = json.loads(window_path().read_text(encoding="utf-8"))["resets_at"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    reset = _parse_reset(stamp)
    now = now or datetime.now(UTC)
    if reset is None or reset <= now:
        return None
    return (reset - WINDOW).astimezone(), reset.astimezone()


def drift(start: datetime, times, now: datetime) -> str:
    """The scheduled time this window should have started at, when it did not.

    "" when it started on schedule, or when no scheduled call falls in
    the last five hours to have anchored it.
    """
    local = now.astimezone()
    expected = None
    for day in (local.date() - timedelta(days=1), local.date()):
        for at in times:
            hour, minute = (int(part) for part in at.split(":"))
            slot = datetime.combine(day, datetime.min.time()).replace(
                hour=hour, minute=minute, tzinfo=local.tzinfo,
            )
            if slot <= local < slot + WINDOW and (expected is None or slot > expected):
                expected = slot
    if expected is None or abs(start - expected) <= _DRIFT_TOLERANCE:
        return ""
    return expected.strftime("%H:%M")
_CALLING = re.compile(r"calling \S+ \((.+)\)$")


def last_by_account(tool: str = keepalive_settings.DEFAULT_TOOL) -> dict:
    """The latest result for each account a call was made for, keyed by its home."""
    try:
        lines = keepalive_settings.log_path(tool).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    latest: dict = {}
    account = None
    for line in lines:
        called = _CALLING.search(line)
        if called:
            account = called.group(1)
        elif account and (" exit " in line or "failed to start" in line):
            latest[account] = line
            account = None
    return latest
