"""What a codex account last reported about its quota, and when to wake it again.

When an account's quota is exhausted the keepalive call fails, and that
failure carries no reset time: its rate limits come back null. Nothing
then anchors the window when the quota returns; it starts whenever
someone next happens to use codex. So every successful keepalive keeps
the limits codex reported for that account, and an exhausted call reads
them back to decide when one extra call for that account alone is worth
scheduling.

Codex does not say which account produced a rollout, and every home
shares one sessions folder. The rollout of a call is the one that
appeared during it and carries the keepalive prompt; anything else is
somebody's own session running at the same time.
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import keepalive_settings

# 用量到這個比例以上的視窗,才是真正讓帳號停擺的那個
HIGH_USE = 90.0
# 紀錄超過這麼久,週視窗至少已經換過一輪,裡面的重置時間不能再信
MAX_AGE = timedelta(days=8)
# 下一個排程時間就在這麼近的話,讓排程自己點火,不必多排一次
SLOT_MARGIN = timedelta(minutes=10)
# 重置時間一到就打,可能比伺服器真的放行早一點
WAKE_DELAY = timedelta(minutes=2)
_WINDOWS = ("five_hour", "weekly")


def limits_path() -> Path:
    return keepalive_settings._state_dir() / "keepalive-codex-limits.json"


def wakes_path() -> Path:
    return keepalive_settings._state_dir() / "keepalive-codex-wakes.json"


def read_json(path: Path, log=None) -> dict:
    """The record at path; {} when it is missing, unreadable or not an object."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if log:
            log(f"could not read {path.name}: {exc}")
        return {}
    if not isinstance(raw, dict):
        if log:
            log(f"could not read {path.name}: not an object")
        return {}
    return raw


def write_json(path: Path, record: dict) -> None:
    """Replace the whole file at once, so an interrupted write leaves the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sessions(home: Path) -> Path:
    # 各帳號的 sessions 可能是指到同一個資料夾的連結
    return (home / "sessions").resolve()


def _day_dirs(sessions: Path, moments) -> set:
    """Date folders a rollout made at these moments could be in.

    Codex files rollouts under YYYY/MM/DD; whether that date is local or
    UTC is not verified, so both, and the days either side of them.
    """
    folders = set()
    for moment in moments:
        for stamp in (datetime.fromtimestamp(moment).astimezone(), datetime.fromtimestamp(moment, UTC)):
            for shift in (-1, 0, 1):
                day = (stamp + timedelta(days=shift)).date()
                folders.add(sessions / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}")
    return folders


def _rollouts(folders) -> set:
    found = set()
    for folder in folders:
        try:
            found.update(path for path in folder.glob("rollout-*.jsonl") if path.is_file())
        except OSError:
            continue
    return found


def snapshot(home: Path, now: "float | None" = None) -> frozenset:
    """The rollouts already there before a call; what it adds is the difference."""
    moment = time.time() if now is None else now
    return frozenset(_rollouts(_day_dirs(_sessions(home), [moment])))


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _lines(path: Path):
    """Each line of a rollout that parses; a torn or garbled line is skipped."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _mentions(path: Path, prompt: str) -> bool:
    # 比對解析後的字串:提示裡有引號或非 ASCII 時,原始行裡是跳脫過的
    try:
        return any(prompt in text for document in _lines(path) for text in _strings(document))
    except OSError:
        return False


def find_rollout(
    home: Path, prompt: str, before: frozenset, started: float, finished: "float | None" = None,
) -> "tuple[Path | None, str]":
    """The rollout one keepalive call made, or None with the reason it is unknown."""
    finished = time.time() if finished is None else finished
    after = _rollouts(_day_dirs(_sessions(home), [started, finished]))
    fresh = []
    for path in after - before:
        try:
            # 一秒的寬限:有些檔案系統的 mtime 只到整秒
            if path.stat().st_mtime >= started - 1:
                fresh.append(path)
        except OSError:
            continue
    matches = [path for path in fresh if _mentions(path, prompt)]
    if not matches:
        return None, "no rollout made during the call carries the keepalive prompt"
    if len(matches) > 1:
        # 兩次 keepalive 同時跑時分不出哪個是哪個帳號的;寧可不記也不記錯
        return None, f"{len(matches)} rollouts made during the call carry the keepalive prompt"
    return matches[0], ""


def _limit_dicts(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "rate_limits" and isinstance(value, dict):
                yield value
            else:
                yield from _limit_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _limit_dicts(value)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _windows(limits: dict) -> dict:
    """The five-hour and weekly windows of one codex rate_limits entry.

    Other limit ids ("premium") and the null windows an exhausted call
    reports say nothing about when the account resets.
    """
    if limits.get("limit_id") != "codex":
        return {}
    found = {}
    for position, default in (("primary", "five_hour"), ("secondary", "weekly")):
        window = limits.get(position)
        if not isinstance(window, dict):
            continue
        used, reset = window.get("used_percent"), window.get("resets_at")
        if not (_number(used) and _number(reset)):
            continue
        minutes = window.get("window_minutes")
        # 以視窗長度為準;沒有長度才照位置(primary 五小時、secondary 一週)
        name = default if not _number(minutes) else ("five_hour" if minutes <= 24 * 60 else "weekly")
        found[name] = {"used_percent": float(used), "resets_at": int(reset)}
    return found


def read_limits(path: Path) -> dict:
    """The last codex limits a rollout reported, or {} when it has none."""
    latest: dict = {}
    for document in _lines(path):
        for limits in _limit_dicts(document):
            windows = _windows(limits)
            if windows:
                latest = windows
    return latest


def remember(home_name: str, windows: dict, now: "datetime | None" = None) -> None:
    """Keep one account's limits beside the others'. Raises OSError when it cannot."""
    moment = now or datetime.now(UTC)
    path = limits_path()
    if path.exists() and not read_json(path):
        # 讀不懂就重寫,會把其他帳號的紀錄一起抹掉;寧可這次不記
        raise OSError(f"{path.name} is unreadable; not overwriting it")
    record = read_json(path)
    earlier = record.get(home_name) if isinstance(record.get(home_name), dict) else {}
    # 這次只回報了一個視窗時,另一個視窗先前記下的重置時間還有用
    record[home_name] = {**earlier, **windows, "recorded_at": int(moment.timestamp())}
    write_json(path, record)


def _stamp(value: object) -> "datetime | None":
    if not _number(value):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def next_slot(times, now: datetime) -> "datetime | None":
    """The first scheduled call after now, in local time."""
    local = now.astimezone()
    slots = []
    for day in (local.date(), local.date() + timedelta(days=1)):
        for at in times:
            hour, minute = (int(part) for part in at.split(":"))
            slot = datetime.combine(day, datetime.min.time()).replace(
                hour=hour, minute=minute,
            ).astimezone()
            if slot > local:
                slots.append(slot)
    return min(slots) if slots else None


def decide(
    record: object, now: datetime, slot: "datetime | None",
) -> "tuple[datetime | None, str]":
    """When to wake an exhausted account, or None and why not.

    The window that stopped the account is the one nearly used up; when
    several are, it is usable only after the last of them resets. With
    none recorded that high, the earliest reset is the best guess.
    """
    if not isinstance(record, dict):
        return None, "no limits recorded from a successful call"
    recorded = _stamp(record.get("recorded_at"))
    if recorded is None:
        return None, "the recorded limits have no time"
    if now - recorded > MAX_AGE:
        return None, f"the recorded limits are from {recorded.astimezone():%m-%d %H:%M}, older than 8 days"
    future = []
    for name in _WINDOWS:
        window = record.get(name)
        if not isinstance(window, dict):
            continue
        reset = _stamp(window.get("resets_at"))
        used = window.get("used_percent")
        if reset is not None and reset > now:
            future.append((reset, float(used) if _number(used) else 0.0))
    if not future:
        return None, "every recorded reset has already passed"
    high = [reset for reset, used in future if used >= HIGH_USE]
    reset = max(high) if high else min(reset for reset, _ in future)
    if slot is not None and reset >= slot - SLOT_MARGIN:
        return None, (
            f"it resets {reset.astimezone():%m-%d %H:%M}, "
            f"not before the next scheduled call at {slot.astimezone():%m-%d %H:%M}"
        )
    return reset + WAKE_DELAY, ""


def pending(now: "datetime | None" = None) -> dict:
    """Each home's one-shot wake that has not come yet."""
    moment = now or datetime.now(UTC)
    found = {}
    for home, entry in read_json(wakes_path()).items():
        at = _stamp(entry.get("at")) if isinstance(entry, dict) else None
        if at is not None and at > moment:
            found[home] = at
    return found


def account_notes(now: "datetime | None" = None) -> dict:
    """One line per codex home: its recorded weekly quota and any pending wake."""
    moment = now or datetime.now(UTC)
    limits = read_json(limits_path())
    wakes = pending(moment)
    notes = {}
    for home in sorted(set(limits) | set(wakes)):
        parts = []
        record = limits.get(home)
        weekly = record.get("weekly") if isinstance(record, dict) else None
        if isinstance(weekly, dict) and _number(weekly.get("used_percent")):
            reset = _stamp(weekly.get("resets_at"))
            recorded = _stamp(record.get("recorded_at"))
            text = f"每週已用 {weekly['used_percent']:g}%"
            if reset is not None:
                text += f",{reset.astimezone():%m-%d %H:%M} 重置"
            if recorded is not None:
                text += f"(記錄於 {recorded.astimezone():%m-%d %H:%M})"
            parts.append(text)
        if home in wakes:
            parts.append(f"預定 {wakes[home].astimezone():%m-%d %H:%M} 單獨喚醒")
        if parts:
            notes[home] = ";".join(parts)
    return notes
