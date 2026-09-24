"""Machine-local Claude Code hooks: one registry, one strip, one install.

A hook's command is an absolute path to this machine's own interpreter, so
it can never travel. But settings.json is a file acg synchronises, so every
hook installed there has to be stripped on the way into the database and
restored on the way out — otherwise one machine's paths reach every other,
which is exactly how a hand-written commit check ended up pointed at
/usr/bin/python3 on a Windows box.

Three features learned that separately, each with its own copy of the strip
and preserve pass, and tools/claude.py chained them one call deep per
feature. A fourth would have meant a fourth layer. They are entries in the
table below instead: acg owns the mechanism, a feature owns only what its
hook does.
"""

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from . import memory
from .paths import CLAUDE_HOME, scheduled_command

PREFIX = "acg："


@dataclass(frozen=True)
class Hook:
    """One machine-local hook: what to call, on which events."""

    name: str
    marker: str
    events: tuple[str, ...]
    command: str
    summary: str
    timeout: int = 10
    matcher: str = ""
    # Some hooks take a fixed argument after the command name.
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    # 有些參數要在安裝當下才算得出來(例如這台的資料庫路徑)
    dynamic_args: "Callable[[], tuple[str, ...]] | None" = None


REGISTRY: dict[str, Hook] = {}


def register(hook: Hook) -> Hook:
    REGISTRY[hook.name] = hook
    return hook


MEMORY_ENTRY = register(Hook(
    name="memory-entry",
    marker=f"{PREFIX}檢查專案日誌入口",
    events=("SessionStart", "UserPromptSubmit"),
    command="__memory-project-entry",
    summary="開會話時修復專案日誌入口(記憶啟用時自動裝)",
    # 入口只認這台的資料庫路徑;少了它 hook 會安靜地什麼都不做
    dynamic_args=lambda: (str(memory.SCRIPT_DIR),),
))

HANDOFF_REMINDER = register(Hook(
    name="handoff-reminder",
    marker=f"{PREFIX}交接用量提醒",
    events=("UserPromptSubmit", "PostToolUse", "PreCompact", "SessionEnd"),
    command="__handoff-reminder",
    timeout=5,
    summary="context 用量到門檻時提醒整理交接(需 statusLine,由 handoff remind 管)",
))

COMMIT_STYLE = register(Hook(
    name="commit-style",
    marker=f"{PREFIX}commit 訊息風格",
    events=("PreToolUse",),
    command="__commit-style",
    matcher="Bash",
    summary="git commit 主旨不符 type(scope): description 就擋下並說明",
))


def _owned_by_any(hook: object) -> bool:
    if not isinstance(hook, dict):
        return False
    message = hook.get("statusMessage")
    return isinstance(message, str) and message.startswith(PREFIX)


def _owned_by(hook: object, marker: str) -> bool:
    return isinstance(hook, dict) and hook.get("statusMessage") == marker


def _strip(document: dict, keep: Callable[[object], bool]) -> dict:
    """Remove every hook entry `keep` rejects, leaving other tools' alone."""
    result = copy.deepcopy(document)
    events = result.get("hooks")
    if not isinstance(events, dict):
        return result
    for event in list(events):
        rows = events.get(event)
        if not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                kept.append(row)
                continue
            hooks = [h for h in row["hooks"] if keep(h)]
            if hooks == row["hooks"]:
                kept.append(row)
            elif hooks:
                kept.append({**row, "hooks": hooks})
        if kept:
            events[event] = kept
        else:
            events.pop(event, None)
    if not events and result.get("hooks") != document.get("hooks"):
        result.pop("hooks", None)
    return result


def without_hooks(document: dict) -> dict:
    """Every acg-owned hook removed — what the database is allowed to see."""
    return _strip(document, lambda hook: not _owned_by_any(hook))


def preserve_hooks(source: dict, target: dict) -> dict:
    """Carry this machine's own hooks across an incoming settings document."""
    result = without_hooks(source)
    events = target.get("hooks", {})
    if not isinstance(events, dict):
        return result
    for event, rows in events.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                continue
            owned = [h for h in row["hooks"] if _owned_by_any(h)]
            if owned:
                result.setdefault("hooks", {}).setdefault(event, []).append(
                    {**row, "hooks": copy.deepcopy(owned)}
                )
    return result


def owned_by(hook: object, marker: str) -> bool:
    return _owned_by(hook, marker)


def without_one(document: dict, hook: Hook) -> dict:
    """Remove just this hook's entries — for a feature that manages its own."""
    return _strip(document, lambda h: not _owned_by(h, hook.marker))


def preserve_one(source: dict, target: dict, hook: Hook) -> dict:
    result = without_one(source, hook)
    events = target.get("hooks", {})
    if not isinstance(events, dict):
        return result
    for event in hook.events:
        rows = events.get(event, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                continue
            owned = [h for h in row["hooks"] if _owned_by(h, hook.marker)]
            if owned:
                result.setdefault("hooks", {}).setdefault(event, []).append(
                    {**row, "hooks": copy.deepcopy(owned)}
                )
    return result


def settings_path():
    return CLAUDE_HOME / "settings.json"


def read_settings() -> dict:
    path = settings_path()
    memory.assert_plain_path(path, directory=False)
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Claude settings.json 必須是物件")  # noqa: TRY004
    return document


def hook_entry(hook: Hook) -> dict:
    # sys.executable 可能是 versions/<版號>/ 裡的實體檔;那個目錄會被清掉,
    # hook 就指向不存在的檔案。固定入口每次更新都會換成新版。
    argv = scheduled_command()
    entry = {
        "type": "command", "command": argv[0],
        "args": argv[1:] + [
            hook.command, *hook.extra_args,
            *(hook.dynamic_args() if hook.dynamic_args else ()),
        ],
        "statusMessage": hook.marker, "timeout": hook.timeout,
    }
    row = {"hooks": [entry]}
    return {"matcher": hook.matcher, **row} if hook.matcher else row


def installed(document: dict, hook: Hook) -> bool:
    for event in hook.events:
        rows = document.get("hooks", {}).get(event)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("hooks"), list) and any(
                _owned_by(h, hook.marker) for h in row["hooks"]
            ):
                return True
    return False


def configure(hook: Hook, enabled: bool) -> bool:
    """Install or remove one hook here; returns whether it ends up installed."""
    from .locking import apply_lock

    with apply_lock():
        document = read_settings()
        result = _strip(document, lambda h: not _owned_by(h, hook.marker))
        if enabled:
            events = result.setdefault("hooks", {})
            if not isinstance(events, dict):
                raise ValueError("Claude hooks 必須是物件")
            for event in hook.events:
                rows = events.setdefault(event, [])
                if not isinstance(rows, list):
                    raise ValueError(f"Claude {event} hooks 必須是陣列")  # noqa: TRY004
                rows.append(copy.deepcopy(hook_entry(hook)))
        if result != document:
            memory._write_text_atomic(
                settings_path(),
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            )
        return installed(result, hook)


def refresh() -> list[str]:
    """Point installed hooks at the current launcher; returns what changed.

    A hook written by an older release can name a version directory that
    has since been pruned, and Claude Code then fails every prompt with
    ENOENT. Only hooks already installed are touched.
    """
    document = read_settings()
    stale = []
    for hook in REGISTRY.values():
        if not installed(document, hook):
            continue
        wanted = hook_entry(hook)["hooks"][0]
        for event in hook.events:
            for row in document.get("hooks", {}).get(event, []):
                for entry in row.get("hooks", []) if isinstance(row, dict) else []:
                    if _owned_by(entry, hook.marker) and (
                        entry.get("command") != wanted["command"]
                        or entry.get("args") != wanted["args"]
                    ):
                        stale.append(hook.name)
    names = list(dict.fromkeys(stale))
    for name in names:
        configure(REGISTRY[name], True)
    return names


def refresh_all() -> None:
    """Best-effort repair after apply/update; a broken settings.json is reported elsewhere."""
    from . import handoff_reminder
    from .console import log_info, log_warn

    try:
        changed = refresh()
        handoff_reminder.refresh()
    except Exception as exc:  # noqa: BLE001 — 順手的修正不能讓 apply/update 失敗
        log_warn(f"hook 路徑沒有更新:{exc}")
        return
    if changed:
        log_info(f"已把 hook 改指向目前的執行檔:{', '.join(changed)}")


def states() -> list[tuple[Hook, bool]]:
    document = read_settings()
    return [(hook, installed(document, hook)) for hook in REGISTRY.values()]
