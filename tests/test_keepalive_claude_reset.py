"""keepalive 每次打 Claude 都要記下五小時視窗幾點重置:看得出這次有沒有開到視窗。"""

import json
import subprocess
from datetime import datetime, timedelta

from ai_config import keepalive_runner


def _stream(resets: datetime, reply: str = "hi") -> str:
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}},
        {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "seven_day",
            "unifiedWindows": {"five_hour": {"resetsAt": int(resets.timestamp()), "status": "allowed"}},
        }},
        {"type": "result", "subtype": "success", "result": reply},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_the_reset_is_read_from_the_rate_limit_event() -> None:
    resets = datetime(2026, 11, 19, 12, 0).astimezone()
    assert keepalive_runner._five_hour_reset(_stream(resets)) == resets


def test_no_event_means_no_reset() -> None:
    assert keepalive_runner._five_hour_reset("not json\n{}\n") is None


def test_a_call_that_opened_the_window_says_so() -> None:
    called = datetime(2026, 11, 19, 7, 0, 51).astimezone()
    note = keepalive_runner._window_note(called, called.replace(second=0) + timedelta(hours=5))
    assert "12:00" in note and "this call opened it" in note


def test_a_call_inside_an_earlier_window_says_when_it_opened() -> None:
    called = datetime(2026, 11, 19, 7, 0, 51).astimezone()
    note = keepalive_runner._window_note(called, datetime(2026, 11, 19, 9, 10).astimezone())
    assert "09:10" in note and "04:10" in note and "did not move it" in note


def test_the_reply_line_comes_from_the_result_event() -> None:
    done = subprocess.CompletedProcess([], 0, _stream(datetime.now().astimezone(), "根本神人"), "")
    assert keepalive_runner._outcome(done, stream_json=True) == "根本神人"


def test_the_claude_call_logs_the_reset(monkeypatch) -> None:
    logged: list = []
    recorded: list = []
    resets = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(hours=5)
    done = subprocess.CompletedProcess([], 0, _stream(resets), "")
    monkeypatch.setattr(keepalive_runner.subprocess, "run", lambda *a, **k: done)
    monkeypatch.setattr(keepalive_runner, "_append_log", lambda line, tool: logged.append(line))
    monkeypatch.setattr("ai_config.keepalive_window.record_window", recorded.append)

    assert keepalive_runner._call_claude(["claude", "-p", "hi"], "claude") == 0

    assert any("this call opened it" in line for line in logged)
    assert recorded[0]["rate_limits"]["five_hour"]["resets_at"] == int(resets.timestamp())


def test_hooks_in_the_call_can_find_node(monkeypatch, tmp_path) -> None:
    """systemd's bare PATH made every `node ...` plugin hook fail in keepalive calls."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(keepalive_runner.Path, "home", lambda: tmp_path)
    env = keepalive_runner._hook_env()
    assert env["PATH"].split(keepalive_runner.os.pathsep)[0] == str(tmp_path / ".local" / "bin")

    monkeypatch.setenv("PATH", env["PATH"])
    assert keepalive_runner._hook_env()["PATH"] == env["PATH"]
