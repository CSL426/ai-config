"""Archive the transcript a handoff leaves behind once someone picks it up.

`/clear` starts a new session under the same name, and the old one's
transcript stays in ~/.claude/projects, where `/resume` lists every
session it finds. Handoff, `/clear`, pickup therefore piles up entries
that all carry the one name. When a pickup closes a thread, the session
that wrote it has been replaced, so its transcript moves out of
projects/ into archived-sessions/: still on disk, off the `/resume` list.

Not at SessionEnd, and not straight after it either: other hooks read
the transcript in the background once the session ends (remember waits
up to half a minute for its lock first), and a file moved from under
them is a lost summary. A transcript written to within SETTLE_SECONDS
is left to a detached process that waits out the rest.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .safety import is_reparse_point

ARCHIVE_DIR_NAME = "archived-sessions"
SETTLE_SECONDS = 60
COMMAND = "__retire-transcript"
# 交接檔從共用記憶同步過來,別台機器寫的內容也會進到這裡;
# 只有長得像 session id 的值才能拿去組路徑
_SESSION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _claude_home() -> Path:
    from .paths import CLAUDE_HOME

    return CLAUDE_HOME


def archive_root() -> Path:
    return _claude_home() / ARCHIVE_DIR_NAME


def _session(value: str) -> str:
    """The id in canonical lowercase, or "" when it is not one.

    Case matters on the way in: NTFS and APFS match file names without it,
    so an upper-case copy of a live id would slip past every comparison
    below and still find that session's transcript.
    """
    value = (value or "").strip()
    return value.lower() if _SESSION_ID.fullmatch(value) else ""


def _is_live(session: str) -> bool:
    """Whether a running Claude Code still records this id as its session.

    /clear keeps the process and swaps the id in its record, so a cleared
    session no longer appears here.
    """
    from .handoff import _sessions_dir

    root = _sessions_dir()
    if not root.is_dir():
        return False
    for path in root.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and str(record.get("sessionId", "")).lower() == session:
            return True
    return False


def _projects() -> "Path | None":
    projects = _claude_home() / "projects"
    if not projects.is_dir() or is_reparse_point(projects):
        return None
    return projects


def _transcripts(session: str) -> list[Path]:
    projects = _projects()
    return sorted(projects.glob(f"*/{session}.jsonl")) if projects else []


def settle_left(value: str, now: "float | None" = None) -> float:
    """Seconds until the transcript has been quiet long enough to move."""
    session = _session(value)
    if not session:
        return 0.0
    now = time.time() if now is None else now
    newest = 0.0
    for transcript in _transcripts(session):
        try:
            newest = max(newest, transcript.stat().st_mtime)
        except OSError:
            continue
    return max(0.0, newest + SETTLE_SECONDS - now) if newest else 0.0


def eligible(value: str, current: str = "") -> bool:
    """A valid id, not this session, and no running session still holds it."""
    session = _session(value)
    if not session or session == (current or "").strip().lower():
        return False
    try:
        return not _is_live(session)
    except OSError:
        return False


def retire(value: str, current: str = "") -> "tuple[list[Path], str]":
    """Move a replaced session's transcript out of `/resume`'s reach.

    Returns what was moved and, when something stood in the way, why.
    Nothing to move (another machine wrote the note, or it was archived
    already) is an empty list and no reason. Never raises: a pickup must
    not fail because its clean-up did.
    """
    if not eligible(value, current):
        return [], ""
    moved: list[Path] = []
    try:
        problem = _move(_session(value), moved)
    except OSError as error:
        problem = f"{type(error).__name__}: {error.strerror or error}"
    return moved, problem


def _move(session: str, moved: list[Path]) -> str:
    """Move each copy; `moved` records progress so a failure midway still reports it."""
    for transcript in _transcripts(session):
        folder = transcript.parent
        if is_reparse_point(folder) or is_reparse_point(transcript):
            return f"略過連結:{transcript}"
        if not transcript.is_file():
            continue
        target = archive_root() / folder.name
        if is_reparse_point(archive_root()) or is_reparse_point(target):
            return f"封存目錄是連結,不搬:{target}"
        sidecar = folder / session
        if sidecar.exists() and (is_reparse_point(sidecar) or not sidecar.is_dir()):
            return f"同名資料夾是連結或不是資料夾,不搬:{sidecar}"
        for destination in (target / transcript.name, target / session):
            if destination.exists() or destination.is_symlink():
                return f"封存目錄已有同名項目:{destination}"
        target.mkdir(parents=True, exist_ok=True)
        # 同名資料夾放的是子代理與工具輸出,跟著 transcript 走;先搬它,
        # 被鎖住的話 transcript 還在原處,/resume 看到的仍是完整的一份
        if sidecar.is_dir():
            sidecar.rename(target / session)
            moved.append(target / session)
        transcript.rename(target / transcript.name)
        moved.append(target / transcript.name)
    return ""


def retire_later(value: str, current: str) -> bool:
    """Leave the move to a detached process once the transcript has settled."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, COMMAND, value, current]
    else:
        command = [sys.executable, "-m", "ai_config", COMMAND, value, current]
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except OSError:
        return False
    return True


def run_detached(args: list[str]) -> int:
    """The detached half: wait for the transcript to settle, then move it."""
    if len(args) != 2:
        return 1
    value, current = args
    # 上限防呆:時間戳在未來的檔案不能讓這個行程一直掛著
    time.sleep(min(settle_left(value), SETTLE_SECONDS))
    _, problem = retire(value, current)
    if problem:
        from .shared_hooks import _log

        _log(COMMAND, problem)
    return 0
