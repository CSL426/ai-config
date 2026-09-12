"""Machine-local hooks restoring the journal's project entry after migration."""

import copy
import json
import os
import re
import sys
import uuid
from pathlib import Path

from . import memory
from .paths import WINDOWS_MODE

COMMAND = "__memory-project-entry"
MARKER = "acg：檢查專案日誌入口"
EVENTS = ("SessionStart", "UserPromptSubmit")


def _owned(hook: object) -> bool:
    return (
        isinstance(hook, dict)
        and hook.get("type") == "command"
        and hook.get("statusMessage") == MARKER
    )


def without_hooks(document: dict) -> dict:
    result = copy.deepcopy(document)
    events = result.get("hooks")
    if not isinstance(events, dict):
        return result
    for event in EVENTS:
        rows = events.get(event)
        if not isinstance(rows, list):
            continue
        if not any(
            isinstance(row, dict)
            and isinstance(row.get("hooks"), list)
            and any(_owned(hook) for hook in row["hooks"])
            for row in rows
        ):
            continue
        kept = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                kept.append(row)
                continue
            remaining = [hook for hook in row["hooks"] if not _owned(hook)]
            if len(remaining) == len(row["hooks"]):
                kept.append(row)
            elif remaining:
                kept.append({**row, "hooks": remaining})
        if kept:
            events[event] = kept
        else:
            events.pop(event, None)
    if not events and result.get("hooks") != document.get("hooks"):
        result.pop("hooks", None)
    return result


def preserve_hooks(source: dict, target: dict) -> dict:
    result = without_hooks(source)
    events = target.get("hooks", {})
    if not isinstance(events, dict):
        return result
    for event in EVENTS:
        for row in events.get(event, []):
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                continue
            owned = [hook for hook in row["hooks"] if _owned(hook)]
            if owned:
                result.setdefault("hooks", {}).setdefault(event, []).append(
                    {**row, "hooks": copy.deepcopy(owned)}
                )
    return result


def settings_path() -> Path:
    return memory.CLAUDE_HOME / "settings.json"


def settings_text(*, enabling: bool) -> str | None:
    path = settings_path()
    memory.assert_plain_path(path, directory=False)
    original = path.read_text(encoding="utf-8-sig") if path.exists() else None
    document = json.loads(original) if original is not None else {}
    if not isinstance(document, dict):
        # The lifecycle reports invalid persisted configuration as ValueError.
        raise ValueError("Claude settings.json must contain an object")  # noqa: TRY004
    result = without_hooks(document)
    if enabling:
        args = []
        if not getattr(sys, "frozen", False):
            args += ["-m", "ai_config"]
        args += [COMMAND, str(memory.SCRIPT_DIR)]
        events = result.setdefault("hooks", {})
        if not isinstance(events, dict):
            raise ValueError("Claude hooks must contain an object")
        for event in EVENTS:
            rows = events.setdefault(event, [])
            if not isinstance(rows, list):
                raise ValueError(f"Claude {event} hooks must contain an array")  # noqa: TRY004
            rows.append({"hooks": [{
                "type": "command", "command": sys.executable, "args": args,
                "statusMessage": MARKER, "timeout": 10,
            }]})
    if result == document:
        return original
    if not result and not enabling:
        return None
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"


def install(*, enabling: bool) -> None:
    path = settings_path()
    text = settings_text(enabling=enabling)
    if text is None:
        if path.exists():
            memory._unlink_file(path)
    elif not path.exists() or path.read_text(encoding="utf-8-sig") != text:
        memory._write_text_atomic(path, text)


def _from_msys_path(text: str) -> "str | None":
    """``/c/Users/x`` -> ``c:\\Users\\x``, or None when it is not that shape.

    The plugin writes the notice from Git Bash, where the journal is spelled
    the MSYS way. Windows cannot open that spelling and normalises it to a
    path on the current drive, so neither comparison below ever matches it.
    """
    # Windows 的 Path() 會先把 / 換成 \,所以兩種分隔符都要認得
    match = re.fullmatch(r"[/\\]([A-Za-z])([/\\].*)?", text)
    if not match:
        return None
    rest = match[2] or "\\"
    return f"{match[1]}:{rest}".replace("/", "\\")


def _notice_points_at(spelled: Path, target: Path) -> bool:
    """Whether the notice names the journal, however it spells the path.

    Three spellings reach this: the real path, the ~/.claude/shared-memory
    link acg knows the directory behind, and the MSYS path Git Bash writes.
    """
    candidates = [spelled]
    if WINDOWS_MODE:
        translated = _from_msys_path(str(spelled))
        if translated is not None:
            candidates.append(Path(translated))
    for candidate in candidates:
        if memory._path_identity(candidate, target):
            return True
        try:
            if os.path.samefile(candidate, target):
                return True
        except OSError:
            continue
    return False


def repair_entry(root: Path) -> bool:
    """Repair only a completed migration; the plugin owns the actual move."""
    root = memory.project_root(root)
    memory.assert_plain_path(root, directory=True)
    entry = memory.project_entry(root)
    if entry is None:
        return False
    state, detail = memory.journal_state(root)
    if state not in {"local", "adopted"}:
        return False
    target = Path(detail)
    memory.assert_plain_path(target, directory=True)
    if not target.is_dir():
        return False
    if memory.is_reparse_point(entry):
        if memory._path_identity(memory._reparse_target(entry), target):
            return False
        raise RuntimeError(f"專案日誌入口指向其他位置，保留原狀：{entry}")
    memory.assert_plain_path(entry, directory=True)
    metadata = {}
    if entry.exists():
        for path in entry.iterdir():
            memory.assert_plain_path(path, directory=False)
            if path.name not in {memory.MIGRATED_NOTE, ".gitignore"}:
                raise RuntimeError(f"專案日誌仍有未搬移內容：{path}")
            metadata[path.name] = path.read_bytes()
        note = metadata.get(memory.MIGRATED_NOTE)
        if note is None:
            raise RuntimeError(f"專案日誌沒有有效搬移通知：{entry}")
        lines = note.decode("utf-8").splitlines()
        if (
            len(lines) != 3
            or lines[0] != "Memory data migrated to:"
            or lines[2] != "This directory is now empty; you may delete it."
            or not _notice_points_at(Path(lines[1].strip()), target)
        ):
            raise RuntimeError(f"專案日誌搬移通知不符目的地：{entry}")
    parked = None
    linked = False
    try:
        if entry.exists():
            parked = entry.with_name(f".remember.acg-{uuid.uuid4().hex}")
            entry.rename(parked)
            if _metadata(parked) != metadata:
                raise RuntimeError("專案日誌在修復時已有變動")
        memory._create_journal_link(target, entry)
        linked = True
        # 要在連結建好後才問 git:專案 .gitignore 常寫 `.remember/`,
        # 只忽略目錄,換成連結後就不再命中
        exclude = memory.project_git_exclude(root)
        if exclude is not None:
            memory.assert_plain_path(exclude[0], directory=False)
            memory._write_text_atomic(*exclude)
    except (OSError, RuntimeError, ValueError) as failure:
        try:
            if linked:
                memory._remove_journal_link(entry, target)
            if parked is not None:
                if os.path.lexists(entry):
                    raise RuntimeError(f"入口已有外部修改；原目錄保留於 {parked}")
                parked.rename(entry)
        except (OSError, RuntimeError) as restore:
            raise memory.JournalRecoveryError(str(restore)) from failure
        raise
    if parked is not None:
        # The parked folder is private to this repair. Never recursively delete
        # it if another writer has added anything after the initial check.
        if _metadata(parked) != metadata:
            raise memory.JournalRecoveryError(f"原目錄有外部修改，保留於 {parked}")
        for name in metadata:
            (parked / name).unlink()
        parked.rmdir()
    return True


def _metadata(path: Path) -> dict[str, bytes]:
    result = {}
    for child in path.iterdir():
        memory.assert_plain_path(child, directory=False)
        result[child.name] = child.read_bytes()
    return result


def run(args: list[str]) -> int:
    """Hook entry point: no transcript reads, no stdout or provider updates."""
    from .locking import apply_lock

    try:
        if len(args) != 1 or Path(args[0]) != memory.SCRIPT_DIR:
            return 0
        payload = sys.stdin.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            return 0
        value = json.loads(payload)
        if not isinstance(value, dict) or not isinstance(value.get("cwd"), str):
            return 0
        root = Path(value["cwd"])
        if not root.is_absolute() or not root.is_dir():
            return 0
        if (
            memory.link_state()[0] != "ok"
            or memory.journal_config_state()[0] != "ours"
            or not memory.has_block(memory._read_text(memory.live_rules_path()))
        ):
            return 0
        with apply_lock(timeout=2):
            memory.preflight_memory()
            repair_entry(root)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"acg：專案日誌入口未修復：{exc}", file=sys.stderr)
        return 1
    return 0
