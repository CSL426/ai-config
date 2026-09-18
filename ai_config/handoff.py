"""Handoffs: one work thread's note to whoever picks it up next.

The journal answers "what happened in this project". It cannot answer
"where did my thread get to", because every session in a project appends
to the same files. A handoff is per-thread instead: one file, written
when a session stops, claimed by the session that continues it.

Claiming is a status line, not a lock: it records a holder so the next
session can see the thread is taken, and refuses a note another session
already holds. Two machines are caught by push refusing to overwrite a
moved upstream.
"""

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .memory import (
    _read_text,
    _write_text_atomic,
    assert_plain_path,
    memory_dir,
    project_key,
)
from .safety import is_reparse_point

HANDOFF_DIR_NAME = "handoff"
OPEN = "open"
CLAIMED = "claimed"
DONE = "done"
_STATES = (OPEN, CLAIMED, DONE)

_FIELD = re.compile(r"^([a-z_]+):\s*(.*)$", re.MULTILINE)
# 只擋路徑分隔符與控制字元。中文工作線名稱要能直接當檔名,不然
# 全部會被濾成同一個 fallback,三條線互相覆蓋
_UNSAFE = re.compile(r"[\x00-\x1f/\\:*?\"<>|]+")
_FRONT = "---\n"


def handoff_dir() -> Path:
    return memory_dir() / HANDOFF_DIR_NAME


def _slug(text: str) -> str:
    cleaned = _UNSAFE.sub("-", text.strip()).strip("-. ")
    # 前後的點會變成隱藏檔或相對路徑;空字串才退回固定名稱
    return cleaned if cleaned and cleaned not in (".", "..") else "thread"


def session_id() -> str:
    """This session's own id, or "" when acg was not run from one."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()


@dataclass
class Handoff:
    path: Path
    thread: str
    project: str
    state: str
    author: str
    claimed_by: str
    created: str
    updated: str
    body: str

    @property
    def name(self) -> str:
        return self.path.stem


def _parse(path: Path) -> "Handoff | None":
    # 這個目錄會同步到 Windows,回來可能帶 CRLF。欄位的正規表示式
    # 錨在 $,留著 \r 會讓每個欄位都讀成空的,整份筆記等於消失
    text = _read_text(path).replace("\r\n", "\n")
    if not text.startswith(_FRONT):
        return None
    _, _, rest = text.partition(_FRONT)
    front, sep, body = rest.partition(_FRONT)
    if not sep:
        return None
    fields = dict(_FIELD.findall(front))
    state = fields.get("state", "").strip()
    updated = fields.get("updated", "").strip()
    return Handoff(
        path=path,
        thread=fields.get("thread", path.stem).strip(),
        project=fields.get("project", "").strip(),
        state=state if state in _STATES else OPEN,
        author=fields.get("author", "").strip(),
        claimed_by=fields.get("claimed_by", "").strip(),
        # 這個欄位比筆記晚出現,舊的沒有。退回 updated 只會讓那幾則
        # 看起來像剛開的,不會讀不出來
        created=fields.get("created", "").strip() or updated,
        updated=updated,
        body=body.strip(),
    )


def _render(note: Handoff) -> str:
    lines = [
        _FRONT,
        f"thread: {note.thread}\n",
        f"project: {note.project}\n",
        f"state: {note.state}\n",
        f"author: {note.author}\n",
    ]
    if note.claimed_by:
        lines.append(f"claimed_by: {note.claimed_by}\n")
    lines.append(f"created: {note.created}\n")
    lines.append(f"updated: {note.updated}\n")
    lines.append(_FRONT)
    return "".join(lines) + "\n" + note.body.strip() + "\n"


def age_in_days(created: str) -> int:
    """Whole days since the thread was opened; 0 when unknown or today."""
    try:
        opened = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return 0
    return max((datetime.now(UTC) - opened).days, 0)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_all(project: str = "") -> list[Handoff]:
    """Every readable handoff, newest first; optionally one project's."""
    root = handoff_dir()
    if not root.is_dir():
        return []
    notes = []
    for path in sorted(root.glob("*.md")):
        if is_reparse_point(path) or not path.is_file():
            continue
        note = _parse(path)
        if note is None:
            continue
        if project and note.project != project:
            continue
        notes.append(note)
    return sorted(notes, key=lambda n: n.updated, reverse=True)


def write(thread: str, body: str, cwd: "Path | None" = None) -> Handoff:
    """Record where this thread got to, for whoever continues it."""
    if not thread.strip():
        raise ValueError("交接需要一個看得懂的工作線名稱")
    if not body.strip():
        raise ValueError("交接內容不能是空的")
    root = handoff_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_slug(thread)}.md"
    assert_plain_path(path, directory=False)
    existing = _parse(path) if path.is_file() else None
    now = _now()
    note = Handoff(
        path=path,
        thread=thread.strip(),
        project=project_key(cwd).key,
        state=OPEN,
        author=session_id() or (existing.author if existing else ""),
        claimed_by="",
        # 重寫一條線是接著做,不是另一條新的線
        created=existing.created if existing else now,
        updated=now,
        body=body,
    )
    _write_text_atomic(path, _render(note))
    return note


def _load_or_fail(name: str) -> Handoff:
    path = handoff_dir() / f"{_slug(name)}.md"
    assert_plain_path(path, directory=False)
    if not path.is_file():
        raise ValueError(f"沒有這則交接:{name}")
    note = _parse(path)
    if note is None:
        raise ValueError(f"交接格式不正確:{path.name}")
    return note


def claim(name: str) -> Handoff:
    """Take over a thread. Refuses a closed one, or one another session holds."""
    note = _load_or_fail(name)
    me = session_id()
    # 結束的線不再是工作。認領它會把紀錄翻回活線並蓋掉持有者,
    # 而會走到這一步的多半是把列表符號讀錯了,不是真的要重開
    if note.state == DONE:
        raise ValueError(f"這則交接已結束:{name}")
    if note.state == CLAIMED and note.claimed_by and note.claimed_by != me:
        raise ValueError(
            f"這則交接已被其他 session 認領:{note.claimed_by}"
        )
    note.state = CLAIMED
    note.claimed_by = me
    note.updated = _now()
    _write_text_atomic(note.path, _render(note))
    return note


def done(name: str) -> Handoff:
    """Mark a thread finished so it stops showing up as work to pick up."""
    note = _load_or_fail(name)
    note.state = DONE
    note.updated = _now()
    _write_text_atomic(note.path, _render(note))
    return note
