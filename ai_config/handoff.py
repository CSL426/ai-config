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

import json
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
ARCHIVE_DIR_NAME = "archive"
# 結束的線還會被回頭查:那個決定當初怎麼下的、驗過什麼。放一個月
# 才收走,夠久到不會擋住還在用的記憶,也不必記得手動清
ARCHIVE_AFTER_DAYS = 30
# 一條線放到隔天還沒人動,寫它的 session 幾乎不可能還在。認領是狀態列
# 不是鎖,逾時就讓下一個人接走,只是要說出前一個持有者是誰
STALE_AFTER_HOURS = 24
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


def archive_dir() -> Path:
    return handoff_dir() / ARCHIVE_DIR_NAME


def _slug(text: str) -> str:
    cleaned = _UNSAFE.sub("-", text.strip()).strip("-. ")
    # 前後的點會變成隱藏檔或相對路徑;空字串才退回固定名稱
    return cleaned if cleaned and cleaned not in (".", "..") else "thread"


def session_id() -> str:
    """This session's own id, or "" when acg was not run from one."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()


def _sessions_dir() -> Path:
    from .paths import CLAUDE_HOME

    return CLAUDE_HOME / "sessions"


def session_name() -> str:
    """The name this session was given, or "" when it has none or it cannot be read.

    The id changes on /clear and the name does not, so the name is what
    ties a handoff to the session that picks it up after clearing.
    Claude Code keeps it in sessions/<pid>.json -- internal state, not an
    interface, so anything unreadable here just means no name.
    """
    me = session_id()
    root = _sessions_dir()
    if not me or not root.is_dir():
        return ""
    for path in root.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("sessionId") == me:
            name = record.get("name")
            return name.strip() if isinstance(name, str) else ""
    return ""


@dataclass
class Handoff:
    path: Path
    thread: str
    project: str
    state: str
    author: str
    claimed_by: str
    session_name: str
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
        session_name=fields.get("session_name", "").strip(),
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
    if note.session_name:
        lines.append(f"session_name: {note.session_name}\n")
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


def hours_since(stamp: str) -> "float | None":
    """Hours since a timestamp; None when it cannot be read."""
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return max((datetime.now(UTC) - then).total_seconds() / 3600, 0.0)


def short_id(value: str, keep: int = 8) -> str:
    """A session id short enough to read, cut at a segment boundary.

    Ids are uuids, whose first hyphenated segment identifies them. A
    blind slice lands mid-segment on anything shaped differently and
    leaves a trailing hyphen that reads as a truncated word.
    """
    if len(value) <= keep:
        return value
    head = value[:keep]
    # 切在分隔符上,而不是固定字元數:切一半的片段看起來像壞掉的字串
    return head.rstrip("-_") if "-" in head or "_" in head else head


def is_stale(note: "Handoff") -> bool:
    """Whether nobody has touched this thread for long enough to doubt it."""
    idle = hours_since(note.updated)
    return idle is not None and idle >= STALE_AFTER_HOURS


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


def archive_finished(older_than_days: int = ARCHIVE_AFTER_DAYS) -> list[str]:
    """Move long-finished threads into archive/, and say which moved.

    A closed thread never lists again, so it only makes the directory
    harder to read. The record is kept, not deleted: closed threads get
    reread months later for why a decision went the way it did.
    """
    root = handoff_dir()
    if not root.is_dir():
        return []
    moved = []
    for note in load_all():
        if note.state != DONE or age_in_days(note.updated) < older_than_days:
            continue
        target = archive_dir() / note.path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        # 工作線名稱是人取的,「收尾」「release」會重複用。直接搬會
        # 讓先歸檔的那份無聲消失,所以撞名就加上結束的日期
        if target.exists():
            stamp = note.updated[:10] or _now()[:10]
            target = target.with_name(f"{note.path.stem}.{stamp}.md")
            spare = 2
            while target.exists():
                target = target.with_name(f"{note.path.stem}.{stamp}-{spare}.md")
                spare += 1
        assert_plain_path(target, directory=False)
        os.replace(note.path, target)
        moved.append(note.thread)
    return moved


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
        session_name=session_name() or (existing.session_name if existing else ""),
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


def _own_thread() -> Handoff:
    """The one live thread this session's name left for it.

    handoff, /clear, pickup: the same name on both ends, so the session
    need not be told which thread is its own. Anything but exactly one
    match is handed back to the person to choose.
    """
    mine = session_name()
    if not mine:
        raise ValueError("讀不到這個 session 的名稱,請指定要認領的工作線")
    candidates = [
        note for note in load_all(project_key().key)
        if note.state != DONE and note.session_name == mine
    ]
    if not candidates:
        raise ValueError(f"沒有 session「{mine}」留下的工作線,請指定要認領的工作線")
    if len(candidates) > 1:
        names = "、".join(note.thread for note in candidates)
        raise ValueError(f"session「{mine}」留下不只一條線:{names},請指定")
    return candidates[0]


def claim(name: str = "") -> "tuple[Handoff, str]":
    """Take over a thread, and say whose stale claim it displaced.

    Refuses a closed thread, or one another session is actively holding.
    A claim nobody has touched for STALE_AFTER_HOURS is taken over
    instead of refused: sessions end without running `done`, and the
    holder recorded on the note is then a session id that no longer
    exists. Refusing on it strands the thread for good.
    """
    note = _load_or_fail(name) if name else _own_thread()
    name = name or note.thread
    me = session_id()
    # 結束的線不再是工作。認領它會把紀錄翻回活線並蓋掉持有者,
    # 而會走到這一步的多半是把列表符號讀錯了,不是真的要重開
    if note.state == DONE:
        raise ValueError(f"這則交接已結束:{name}")
    displaced = ""
    if note.state == CLAIMED and note.claimed_by and note.claimed_by != me:
        if not is_stale(note):
            raise ValueError(
                f"這則交接已被其他 session 認領:{note.claimed_by}"
            )
        displaced = note.claimed_by
    note.state = CLAIMED
    note.claimed_by = me
    note.updated = _now()
    _write_text_atomic(note.path, _render(note))
    return note, displaced


def done(name: str) -> Handoff:
    """Mark a thread finished so it stops showing up as work to pick up."""
    note = _load_or_fail(name)
    note.state = DONE
    note.updated = _now()
    _write_text_atomic(note.path, _render(note))
    return note


_HEADING = re.compile(r"^#{1,3}\s*(.+?)\s*$", re.MULTILINE)
# 接手的人最先要問的是「還剩什麼」,其次才是「有什麼還不確定」
_WANTED = ("next", "下一步", "unknowns", "待決定", "待辦")


def summary(body: str, limit: int = 70) -> str:
    """The line worth showing in a list: what is left, else the opening.

    A thread written as one prose block hides its open items — one here
    ran to ten thousand characters with three of them buried inside. A
    note that names them under a heading can say so in the list instead.
    """
    sections = {}
    marks = list(_HEADING.finditer(body))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        sections[mark.group(1).strip().casefold()] = body[mark.end():end].strip()
    for wanted in _WANTED:
        for name, text in sections.items():
            if not name.startswith(wanted) or not text:
                continue
            # 待辦常寫成「1. **做 X**。說明」;只剝開頭會留下半個粗體記號
            first = text.splitlines()[0].replace("**", "").lstrip("-* ").strip()
            if first:
                return f"{mark_label(name)}{first}"[:limit]
    # 退回第一行時要跳過標題:「## 這條線在做什麼」本身什麼都沒說
    lines = [
        line.strip() for line in body.splitlines()
        if line.strip() and not _HEADING.match(line)
    ]
    return (lines[0] if lines else "")[:limit]


def mark_label(heading: str) -> str:
    return "還剩:" if heading.startswith(("next", "下一步")) else "未定:"
