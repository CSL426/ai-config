"""Which data-repository commit this machine's live configuration matches.

A push gathers the live configuration into the repository. On a machine
that has not applied the latest configuration, that gather writes the
machine's older copy over settings another machine pushed since: on one
machine a push all nearly reverted a day of skill fixes. Each apply and
push records the commit the live configuration now matches; a later push
asks before gathering over commits made after it.
"""

import json
import os
import subprocess
from pathlib import Path

from .console import log_info, log_warn
from .paths import ALL_TOOLS, HOME, SCRIPT_DIR


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "acg" / "applied.json"


def _git(*args: str) -> "str | None":
    try:
        done = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _load() -> dict:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _repo_key() -> str:
    # 換過資料庫的機器,舊庫的 commit 對新庫沒有意義
    return str(SCRIPT_DIR.resolve())


def record(tools: "list[str]") -> None:
    """Remember that these tools' live configuration matches HEAD now."""
    head = (_git("rev-parse", "HEAD") or "").strip()
    tools = [tool for tool in tools if tool in ALL_TOOLS]
    if not head or not tools:
        return
    state = _load()
    entry = state.get(_repo_key())
    entry = entry if isinstance(entry, dict) else {}
    entry.update(dict.fromkeys(tools, head))
    state[_repo_key()] = entry
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # 記不下來只是少一層保護,不能讓 apply/push 失敗


def has_record() -> bool:
    """Whether this machine ever applied or pushed this data repository."""
    entry = _load().get(_repo_key())
    return isinstance(entry, dict) and any(entry.get(tool) for tool in ALL_TOOLS)


def repo_has_config(data_dir: Path) -> bool:
    """Whether any tool has saved configuration; an empty folder or .gitkeep is not."""
    for tool in ALL_TOOLS:
        root = data_dir / tool
        if root.is_dir() and any(
            item.is_file() and item.name != ".gitkeep" for item in root.rglob("*")
        ):
            return True
    return False




def _recorded_commit(tool: str) -> "str | None":
    entry = _load().get(_repo_key())
    recorded = entry.get(tool) if isinstance(entry, dict) else None
    return recorded if isinstance(recorded, str) and recorded else None


def _z_list(*args: str) -> "list[str] | None":
    out = _git(*args)
    return None if out is None else [item for item in out.split("\0") if item]


def _dirty_paths(tool: str) -> "set[str] | None":
    entries = _z_list("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", f"{tool}/")
    return None if entries is None else {entry[3:] for entry in entries if len(entry) > 3}


def _blob(revision: str, path: str) -> "str | None":
    out = _git("rev-parse", "--verify", "-q", f"{revision}:{path}")
    return out.strip() if out else None


def _worktree_blob(path: str) -> "str | None":
    if not (SCRIPT_DIR / path).is_file():
        return None
    out = _git("hash-object", "--", path)
    return out.strip() if out else None


def _set_aside(path: str, stamp: str) -> "Path | None":
    """Keep this machine's copy of a file both machines changed; nothing is lost."""
    from .paths import BACKUP_BASE

    source = SCRIPT_DIR / path
    if not source.is_file():
        return None
    target = BACKUP_BASE / "push-kept-repo" / stamp / path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    except OSError:
        return None
    return target


class UnknownBase(Exception):
    """The commit this machine last matched is gone from the repository's history."""


def keep_newer_repo_files(tools: "list[str]", overwrite: bool = False) -> "list[tuple[str, str]]":
    """After a gather, put back every file another machine changed since this one synced.

    A machine that never applied an update gathers its older copy, and a
    push used to commit that over the update; the question it asked named
    commits, not files, and a yes reverted a skill fix (2026-11-13). Now
    it is decided per file: a file the repository changed since this
    machine last matched it keeps the repository's version. If this
    machine changed it too, its copy is set aside under the backup root
    and the user is told. ``--overwrite-newer`` keeps this machine's copy.
    Returns (path, "stale" | "both") for every file put back. Raises
    UnknownBase when the recorded commit cannot be compared (history
    rewritten): guessing would push the older copy again.
    """
    from datetime import datetime

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H%M%S")
    kept: list[tuple[str, str]] = []
    for tool in tools:
        recorded = _recorded_commit(tool)
        if not recorded:
            continue  # 從沒 apply/push 過:無從判斷,不擋
        # --no-renames:改名只列新路徑的話,這台收回來的舊路徑會被當成新增而復活
        changed = _z_list(
            "diff", "--name-only", "--no-renames", "-z", recorded, "HEAD", "--", f"{tool}/"
        )
        dirty = _dirty_paths(tool)
        if changed is None or dirty is None:
            if overwrite:
                continue
            raise UnknownBase(f"{tool}:找不到這台上次同步的版本({recorded[:7]}),無法逐檔比對")
        for path in sorted(set(changed) & dirty):
            here, before = _worktree_blob(path), _blob(recorded, path)
            kind = "stale" if here == before else "both"
            if overwrite:
                log_warn(f"--overwrite-newer:{path} 用這台的版本蓋過資料庫較新的版本")
                continue
            if kind == "both":
                saved = _set_aside(path, stamp)
                if saved is None and here is not None:
                    log_warn(f"{path}:兩邊都改過,這台的版本存不下來,先不動它")
                    continue
            if _blob("HEAD", path):
                if _git("checkout", "HEAD", "--", path) is None:
                    log_warn(f"{path}:還原資料庫版本失敗,先不動它")
                    continue
            else:
                try:
                    (SCRIPT_DIR / path).unlink()
                except OSError:
                    log_warn(f"{path}:資料庫已刪除,但這台的檔案移不掉,先不動它")
                    continue
            kept.append((path, kind))
            if kind == "stale":
                log_info(f"{path}:這台還沒套用別台的更新,保留資料庫的版本(之後 apply 會更新這台)")
            elif here is None:
                log_warn(f"{path}:這台刪掉了、別台又改過,保留資料庫的版本")
            else:
                log_warn(f"{path}:這台和別台都改過,保留資料庫的版本;這台的版本存到 {saved}")
    return kept
