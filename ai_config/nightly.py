"""One scheduled run a night: update the tools, then save memory with the new acg.

Updating and saving used to be two schedules. Bound together, the save
runs with whatever acg the update just installed, so a fix released
during the day takes effect that same night: arm-box's journal sat blocked
by a false credential match for a night after the fix was already out.

Each half keeps its own switch. Saving memory and replacing executables
are different things to agree to, so turning one on never turns on the
other; the schedule exists while either is on. It is the timer autopush
has always had (unit acg-autopush, task "acg memory autopush"), so
machines that never enable updates keep running exactly what they ran.
"""

import json
import os
import subprocess

from . import autopush, paths

FEATURES = ("autopush", "autoupdate")
_LABELS = {"autopush": "每天自動上傳記憶", "autoupdate": "每天自動更新"}
_PUSH_TIMEOUT = 900


def _settings_path():
    return autopush.state_path().with_name("nightly.json")


def _load() -> dict:
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if k in FEATURES and isinstance(v, bool)} if isinstance(raw, dict) else {}


def _save(flags: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(flags), encoding="utf-8")


def enabled(feature: str) -> bool:
    if not autopush.schedule_installed():
        return False
    value = _load().get(feature)
    if value is None:
        # 綁定之前裝的排程只會上傳,沒有設定檔就照那樣解讀
        return feature == "autopush"
    return value


def _flags() -> dict:
    return {feature: enabled(feature) for feature in FEATURES}


def turn_on(feature: str, hour: "int | None" = None) -> list:
    """Install (or move) the nightly schedule, then remember this half is wanted.

    Saved after the install so a scheduler that refuses leaves the switch
    where it was.
    """
    flags = _flags()
    lines = autopush.install(hour)
    flags[feature] = True
    _save(flags)
    return lines


def turn_off(feature: str) -> list:
    flags = _flags()
    flags[feature] = False
    others = [f for f in FEATURES if flags[f]]
    if others:
        _save(flags)
        return [f"已關閉{_LABELS[feature]};每晚排程留給{'、'.join(_LABELS[f] for f in others)}"]
    lines = autopush.uninstall()
    _save(flags)
    return lines


def run(stale_hours: float) -> int:
    """What the timer runs. Both halves always get their turn."""
    code = 0
    if enabled("autoupdate"):
        from . import autoupdate

        code = autoupdate.run()
    if enabled("autopush"):
        code = max(code, _push_with_installed_acg(stale_hours))
    return code


def _push_with_installed_acg(stale_hours: float) -> int:
    """Push through the launcher, which now points at whatever was just installed.

    Pushing in this process would use the version that started the night,
    and the point of running after the update is not to.
    """
    try:
        done = subprocess.run(
            [*paths.scheduled_command(), "memory", "push", "--if-stale", f"{stale_hours:g}"],
            check=False, stdin=subprocess.DEVNULL, timeout=_PUSH_TIMEOUT,
            env={**os.environ, "AI_CONFIG_NO_UPDATE_CHECK": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        print(f"✗ 記憶上傳 {_PUSH_TIMEOUT // 60} 分鐘內沒有結束", flush=True)
        return 1
    except OSError as exc:
        print(f"✗ 無法啟動記憶上傳:{exc}", flush=True)
        return 1
    return done.returncode
