"""Shared memory: one notebook that Claude Code, Codex and agy all read.

The data repository already travels between machines, so the notebook
lives inside it as an ordinary tracked directory. Every tool gets the
same short instruction block pointing at one link, ~/.claude/shared-memory,
so the block is a constant that can itself be synced inside CLAUDE.md.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from .links import _path_identity, _reparse_target, _try_create_junction
from .paths import (
    AGY_CONFIG_RULES,
    CLAUDE_HOME,
    CODEX_HOME,
    HOME,
    MEMORY_DIR_NAME,
    MEMORY_LINK,
    NATIVE_WINDOWS,
    SCRIPT_DIR,
    claude_source_dir,
)
from .safety import codex_agents_shared_target, is_reparse_point

WRITE_OBSERVER: ContextVar = ContextVar("memory_write_observer", default=None)

INDEX_NAME = "MEMORY.md"
TOPICS_NAME = "topics"
PROJECTS_NAME = "projects"
AGY_RULES_NAME = "acg-memory.md"
JOURNAL_DIR_NAME = "journal"
# remember plugin 只讀使用者全域設定裡的 data_dir,{slug} 是它能給的唯一專案變數
REMEMBER_USER_CONFIG = HOME / ".remember" / "config.json"
REMEMBER_PLUGIN_CACHE = (
    CLAUDE_HOME / "plugins" / "cache" / "claude-plugins-official" / "remember"
)
JOURNAL_TEMPLATE = "~/.claude/shared-memory/journal/{slug}"
MIGRATED_NOTE = "MIGRATED-TO.txt"
# 日誌裡只有摘要值得跨機器;緩衝、鎖與 log 都是本機當下的狀態
JOURNAL_GITIGNORE = """logs/
tmp/
now.md
.*
!.gitignore
"""
BLOCK_BEGIN = "<!-- acg:memory:begin -->"
BLOCK_END = "<!-- acg:memory:end -->"

# 規則文字是常數:路徑不隨機器改變,所以可以直接放進同步的 CLAUDE.md
RULES_BLOCK = f"""{BLOCK_BEGIN}
## 共用記憶(由 acg 管理,請勿手動修改此區塊)

- 開始工作前先讀 `~/.claude/shared-memory/MEMORY.md`。目錄不存在就略過整節。
- 判斷專案鍵值:執行 `git remote get-url origin`,取網址最後兩段 `owner/repo`
  (去掉 `.git`),寫成 `owner--repo`。沒有遠端就用專案目錄名稱。
- 若 `~/.claude/shared-memory/projects/<鍵值>/MEMORY.md` 存在也讀它,再依兩份
  索引按需讀取各自 `topics/` 裡的主題文件。
- 只有使用者明確要求「記住」時才寫入,且只寫進這個共用目錄,不寫進工具
  自己的自動記憶。
- 講這個程式庫的決定、陷阱或進度就寫專案層;講使用者偏好或跨專案的事實
  就寫全域層;分不清就問使用者。
- 每則記錄寫結論、日期、適用範圍與來源。不寫憑證、聊天紀錄或暫時狀態,
  避免重複與未證實的猜測。
- 專案層目錄不存在時自行建立 MEMORY.md。`acg memory path` 會印出兩層路徑。
- 若 `~/.claude/shared-memory/projects/<鍵值>/journal/recent.md` 或專案目錄的
  `.remember/recent.md` 存在,讀它了解最近進度。那是 Claude 的工作日誌,唯讀。
{BLOCK_END}
"""

INDEX_TEMPLATE = """# 共用記憶

全域索引。每則一行:`- [標題](topics/檔名.md) — 一句話摘要`。
專案記憶放在 `projects/<owner--repo>/MEMORY.md`,格式相同。
"""

_BLOCK_RE = re.compile(
    re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.DOTALL
)
# 遠端網址最後兩段:git@host:owner/repo.git、https://host/owner/repo、/srv/owner/repo
_REMOTE_TAIL = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def memory_dir() -> Path:
    return SCRIPT_DIR / MEMORY_DIR_NAME


def index_path() -> Path:
    return memory_dir() / INDEX_NAME


def live_rules_path() -> Path:
    return CLAUDE_HOME / "CLAUDE.md"


def source_rules_path() -> Path:
    return claude_source_dir() / "CLAUDE.md"


def agy_rules_path() -> Path:
    return AGY_CONFIG_RULES / AGY_RULES_NAME


def codex_override_path() -> Path:
    return CODEX_HOME / "AGENTS.override.md"


def codex_rules_path() -> Path:
    return CODEX_HOME / "AGENTS.md"


def instruction_paths() -> list[Path]:
    paths = [live_rules_path(), codex_rules_path()]
    for path in (source_rules_path(), SCRIPT_DIR / "codex" / "AGENTS.md"):
        if os.path.lexists(path):
            paths.append(path)
    return paths


def assert_plain_path(path: Path, *, directory: bool | None = None) -> None:
    """Validate missing destinations too, without following a reparse point."""
    path = Path(os.path.abspath(path))
    for parent in reversed(path.parents):
        if is_reparse_point(parent):
            raise RuntimeError(f"Refusing reparse point parent: {parent}")
        if parent.exists() and not parent.is_dir():
            raise RuntimeError(f"Expected directory: {parent}")
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point: {path}")
    if not path.exists():
        return
    mode = path.stat().st_mode
    valid = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
    if directory is None:
        valid = stat.S_ISDIR(mode) or stat.S_ISREG(mode)
    if not valid:
        raise RuntimeError(f"Unexpected path type: {path}")


def rules_target(path: Path) -> Path:
    if path == codex_rules_path() and is_reparse_point(path):
        assert_plain_path(path.parent, directory=True)
        target = codex_agents_shared_target(path)
        assert target is not None
    else:
        target = path
    assert_plain_path(target, directory=False)
    return target


def _assert_tree(path: Path) -> None:
    assert_plain_path(path)
    if path.is_dir():
        for child in path.iterdir():
            _assert_tree(child)


def preflight_memory() -> None:
    assert_plain_path(memory_dir(), directory=True)
    for path in (index_path(), memory_dir() / ".gitignore"):
        assert_plain_path(path, directory=False)
    for path in (memory_dir() / TOPICS_NAME, memory_dir() / PROJECTS_NAME):
        assert_plain_path(path, directory=True)
        _assert_tree(path)
    assert_plain_path(journal_root(), directory=True)
    if journal_root().is_dir():
        for path in journal_root().iterdir():
            if is_reparse_point(path):
                target = _reparse_target(path)
                if target.name.casefold() != JOURNAL_DIR_NAME or not _path_identity(
                    target.parent.parent, memory_dir() / PROJECTS_NAME
                ):
                    raise RuntimeError(f"Unexpected journal link: {path}")
                assert_plain_path(target, directory=True)
                if not target.is_dir():
                    raise RuntimeError(f"Broken journal link: {path}")
            else:
                _assert_tree(path)


def preflight_rules(*, enabling: bool) -> None:
    for path in instruction_paths():
        target = rules_target(path)
        _validate_block(_read_text(target))
    path = agy_rules_path()
    assert_plain_path(path, directory=False)
    text = _read_text(path)
    _validate_block(text)
    if enabling and path.exists() and not has_block(text):
        raise RuntimeError(f"Refusing unmanaged agy rules file: {path}")
    assert_plain_path(REMEMBER_USER_CONFIG, directory=False)
    _read_user_config()
    assert_plain_path(MEMORY_LINK.parent, directory=True)
    state, detail = link_state()
    if enabling and state not in ("missing", "ok"):
        raise RuntimeError(f"無法建立連結 {MEMORY_LINK}:{detail}")


# ─── project key ───────────────────────────────────────────────


@dataclass
class ProjectKey:
    key: str
    # 由遠端網址推導的鍵值在每台機器上都相同;目錄名稱則不保證
    stable: bool
    source: str


def sanitize_key(name: str) -> str:
    return _UNSAFE.sub("-", name.strip()).strip("-.") or "unnamed"


def parse_project_key(remote_url: str) -> str:
    match = _REMOTE_TAIL.search(remote_url.strip())
    if not match:
        return ""
    return f"{sanitize_key(match[1])}--{sanitize_key(match[2])}"


def _git_output(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def project_key(cwd: "Path | None" = None) -> ProjectKey:
    here = Path.cwd() if cwd is None else cwd
    remote = _git_output(here, "remote", "get-url", "origin")
    if remote:
        key = parse_project_key(remote)
        if key:
            return ProjectKey(key, True, "remote")
    toplevel = _git_output(here, "rev-parse", "--show-toplevel")
    if toplevel:
        return ProjectKey(sanitize_key(Path(toplevel).name), False, "toplevel")
    return ProjectKey(sanitize_key(here.resolve().name), False, "cwd")


def project_memory_dir(key: ProjectKey) -> Path:
    return memory_dir() / PROJECTS_NAME / key.key


def project_root(cwd: "Path | None" = None) -> Path:
    """The directory Claude Code keys its per-project state to.

    For a linked worktree that is the main checkout, matching what the
    remember plugin does, so every worktree shares one journal.
    """
    here = Path.cwd() if cwd is None else cwd
    common = _git_output(
        here, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if common and Path(common).name == ".git":
        return Path(common).parent
    toplevel = _git_output(here, "rev-parse", "--show-toplevel")
    if toplevel:
        return Path(toplevel)
    return here.resolve()


def session_slug(path: Path) -> str:
    """Claude Code's name for a project directory under ~/.claude/projects/."""
    out = []
    for char in str(path).rstrip("/\\") or str(path):
        if char.isascii() and char.isalnum():
            out.append(char)
        elif ord(char) >= 0x10000:
            out.append("--")
        else:
            out.append("-")
    return "".join(out)


# ─── instruction block ────────────────────────────────────────


def has_block(text: str) -> bool:
    return _BLOCK_RE.search(text) is not None


def _validate_block(text: str) -> None:
    if BLOCK_BEGIN not in text and BLOCK_END not in text:
        return
    if (
        text.count(BLOCK_BEGIN) != 1
        or text.count(BLOCK_END) != 1
        or not has_block(text)
    ):
        raise RuntimeError("Malformed acg memory instruction block")


def without_block(text: str) -> str:
    stripped = _BLOCK_RE.sub("", text)
    return stripped.rstrip("\n") + "\n" if stripped.strip() else ""


def with_block(text: str) -> str:
    body = without_block(text)
    if body:
        return body + "\n" + RULES_BLOCK
    return RULES_BLOCK


def _write_text_atomic(path: Path, content: str) -> None:
    assert_plain_path(path, directory=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_bytes() if path.exists() else None
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        assert_plain_path(path, directory=False)
        current = path.read_bytes() if path.exists() else None
        if current != previous:
            raise RuntimeError(f"File changed during update: {path}")
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
        observer = WRITE_OBSERVER.get()
        if observer is not None:
            observer(path, content.encode("utf-8"))
    finally:
        temporary.unlink(missing_ok=True)


def _unlink_file(path: Path) -> None:
    assert_plain_path(path, directory=False)
    path.unlink()
    observer = WRITE_OBSERVER.get()
    if observer is not None:
        observer(path, None)


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    # Path.read_text 在 3.13 之前沒有 newline 參數;保留原始換行才不會改動整個檔案
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def install_block(path: Path) -> bool:
    """Append the block once; return whether the file changed."""
    path = rules_target(path)
    current = _read_text(path)
    _validate_block(current)
    updated = with_block(current)
    if updated == current:
        return False
    _write_text_atomic(path, updated)
    return True


def remove_block(path: Path) -> bool:
    path = rules_target(path)
    if not path.is_file():
        return False
    current = _read_text(path)
    _validate_block(current)
    if not has_block(current):
        return False
    _write_text_atomic(path, without_block(current))
    return True


def install_agy_rules() -> bool:
    path = agy_rules_path()
    assert_plain_path(path, directory=False)
    if path.exists() and not has_block(_read_text(path)):
        raise RuntimeError(f"Refusing unmanaged agy rules file: {path}")
    return install_block(path)


def remove_agy_rules() -> bool:
    path = agy_rules_path()
    assert_plain_path(path, directory=False)
    if not path.is_file() or not has_block(_read_text(path)):
        return False
    remove_block(path)
    if not _read_text(path).strip():
        _unlink_file(path)
    return True


# ─── link ─────────────────────────────────────────────────────


def link_state() -> tuple[str, str]:
    """One of ok / missing / foreign / conflict, plus a detail for the last two."""
    link = MEMORY_LINK
    if not (link.exists() or link.is_symlink() or is_reparse_point(link)):
        return "missing", ""
    if link.is_symlink() or is_reparse_point(link):
        try:
            current = _reparse_target(link)
        except (OSError, RuntimeError) as exc:
            return "foreign", str(exc)
        if _path_identity(current, memory_dir()):
            return "ok", ""
        return "foreign", str(current)
    return "conflict", "已存在同名的一般目錄或檔案"


def create_link() -> tuple[bool, str]:
    assert_plain_path(memory_dir(), directory=True)
    assert_plain_path(MEMORY_LINK.parent, directory=True)
    state, detail = link_state()
    if state == "ok":
        return True, ""
    if state != "missing":
        return False, detail or state
    target = memory_dir()
    target.mkdir(parents=True, exist_ok=True)
    CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
    if NATIVE_WINDOWS:
        if _try_create_junction(target, MEMORY_LINK):
            return True, ""
        return False, "無法建立 Junction"
    try:
        MEMORY_LINK.symlink_to(target)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def remove_link() -> bool:
    state, _ = link_state()
    if state != "ok":
        return False
    if MEMORY_LINK.is_symlink():
        MEMORY_LINK.unlink()
    else:
        os.rmdir(MEMORY_LINK)
    return True


# ─── journal: the remember plugin's per-project log ──────────


def remember_installed() -> bool:
    return REMEMBER_PLUGIN_CACHE.is_dir()


def _read_user_config() -> "dict | None":
    if not REMEMBER_USER_CONFIG.is_file():
        return None
    try:
        data = json.loads(_read_text(REMEMBER_USER_CONFIG))
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid remember configuration: {REMEMBER_USER_CONFIG}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(  # noqa: TRY004 - report malformed user input at CLI boundary
            f"Expected remember config object: {REMEMBER_USER_CONFIG}"
        )
    return data


def journal_config_state() -> tuple[str, str]:
    """ours / unset / other, plus the current value for other."""
    config = _read_user_config()
    value = (config or {}).get("data_dir", "")
    if value == JOURNAL_TEMPLATE:
        return "ours", value
    if not value or value == ".remember":
        return "unset", value
    return "other", str(value)


def install_journal_config() -> tuple[bool, str]:
    """Point the remember plugin at the shared journal root; keep other keys."""
    state, value = journal_config_state()
    if state == "ours":
        return False, ""
    if state == "other":
        return False, value
    config = _read_user_config() or {}
    config["data_dir"] = JOURNAL_TEMPLATE
    _write_text_atomic(
        REMEMBER_USER_CONFIG,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    )
    return True, ""


def remove_journal_config() -> bool:
    state, _ = journal_config_state()
    if state != "ours":
        return False
    config = _read_user_config() or {}
    config.pop("data_dir", None)
    if config:
        _write_text_atomic(
            REMEMBER_USER_CONFIG,
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        )
    else:
        _unlink_file(REMEMBER_USER_CONFIG)
    return True


def journal_root() -> Path:
    return memory_dir() / JOURNAL_DIR_NAME


def journal_link(root: Path) -> Path:
    return journal_root() / session_slug(root)


def project_journal_dir(key: ProjectKey) -> Path:
    return project_memory_dir(key) / JOURNAL_DIR_NAME


def ensure_journal_ignored() -> bool:
    """journal/<slug> links are per machine; only projects/<key>/journal syncs."""
    ignore = memory_dir() / ".gitignore"
    current = _read_text(ignore)
    # 一定要錨定:沒有斜線開頭的 journal/ 會連 projects/<key>/journal/ 一起忽略
    rule = f"/{JOURNAL_DIR_NAME}/"
    lines = [line for line in current.splitlines() if line != f"{JOURNAL_DIR_NAME}/"]
    if rule in lines:
        return False
    lines.append(rule)
    _write_text_atomic(ignore, "\n".join(lines) + "\n")
    return True


def _check_journal_tree(path: Path) -> None:
    """Reject links before moving anything, including links inside subfolders."""
    assert_plain_path(path, directory=True)
    if not path.exists():
        return
    for entry in path.iterdir():
        assert_plain_path(entry)
        if entry.is_dir():
            _check_journal_tree(entry)


def _journal_destination(destination: Path, name: str, origin: str) -> Path:
    candidate = destination / name
    suffix = 0
    while candidate.exists() or candidate.is_symlink() or is_reparse_point(candidate):
        suffix += 1
        extra = "" if suffix == 1 else f"-{suffix}"
        candidate = destination / f"{name}.from-{origin}{extra}"
    assert_plain_path(candidate)
    return candidate


class JournalRecoveryError(RuntimeError):
    recovery_required = True


def _journal_fingerprint(path: Path) -> str:
    """A content signature used to avoid undoing someone else's journal edit."""
    _assert_tree(path)
    digest = hashlib.sha256()
    if not path.exists():
        return "missing"
    entries = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
    for entry in entries:
        digest.update(str(entry.relative_to(path)).encode("utf-8"))
        digest.update(b"directory" if entry.is_dir() else b"file")
        if entry.is_file():
            digest.update(entry.read_bytes())
    return digest.hexdigest()


def _restore_journal_moves(moves: list[tuple[Path, Path, str]]) -> list[str]:
    errors: list[str] = []
    for source, destination, signature in reversed(moves):
        try:
            assert_plain_path(source)
            assert_plain_path(destination)
            if not destination.exists():
                continue
            if source.exists():
                # A failed cross-device copy can leave both copies. Keep both.
                errors.append(f"保留來源與搬移副本:{source} / {destination}")
                continue
            if _journal_fingerprint(destination) != signature:
                errors.append(f"日誌已被外部修改，保留於 {destination}")
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            try:
                destination.rename(source)
            except OSError:
                shutil.move(str(destination), str(source))
        except (OSError, RuntimeError) as exc:
            errors.append(f"資料保留於 {destination}:{exc}")
    return errors


def _move_contents(
    source: Path,
    destination: Path,
    *,
    include_metadata: bool = False,
    moves: list[tuple[Path, Path, str]] | None = None,
) -> int:
    """Preserve collisions and retain enough information to undo later failures."""
    _check_journal_tree(source)
    _check_journal_tree(destination)
    if (
        source == destination
        or source in destination.parents
        or destination in source.parents
    ):
        raise RuntimeError("日誌來源與目的地不能重疊")
    destination.mkdir(parents=True, exist_ok=True)
    own_moves = moves is None
    records = [] if moves is None else moves
    count = 0
    try:
        for entry in sorted(source.iterdir()):
            if not include_metadata and entry.name in (".gitignore", MIGRATED_NOTE):
                continue
            target = _journal_destination(destination, entry.name, source.parent.name)
            records.append((entry, target, _journal_fingerprint(entry)))
            shutil.move(str(entry), str(target))
            count += 1
    except (OSError, RuntimeError) as exc:
        if own_moves:
            errors = _restore_journal_moves(records)
            if errors:
                raise JournalRecoveryError("日誌搬移失敗;" + ";".join(errors)) from exc
        raise
    return count


def _create_journal_link(target: Path, link: Path) -> None:
    assert_plain_path(link)
    if NATIVE_WINDOWS:
        if not _try_create_junction(target, link):
            raise RuntimeError(f"無法建立 Junction:{link}")
    else:
        link.symlink_to(target)


def _remove_journal_link(link: Path, target: Path) -> None:
    assert_plain_path(link.parent, directory=True)
    if not (link.is_symlink() or is_reparse_point(link)):
        return
    if not _path_identity(_reparse_target(link), target):
        raise RuntimeError(f"日誌連結已指向別處:{link}")
    if link.is_symlink():
        link.unlink()
    else:
        os.rmdir(link)


def legacy_journal_dir(root: Path) -> "Path | None":
    """Where the plugin kept this project's journal before adopt, or None.

    At HOME the directory is the plugin's own config home, not a journal;
    the plugin itself refuses to migrate it, and so must we.
    """
    if root.resolve() == HOME.resolve():
        return None
    return root / ".remember"


def _is_legacy_journal(legacy: "Path | None") -> bool:
    """A plain .remember folder the plugin filled before adopt.

    A linked .remember is acg's own entry (or somebody else's link), never
    content to migrate.
    """
    return (
        legacy is not None
        and legacy.is_dir()
        and not (legacy.is_symlink() or is_reparse_point(legacy))
        and not (legacy / MIGRATED_NOTE).is_file()
    )


# ─── project entry: <project>/.remember shows the journal ─────


def project_entry(root: Path) -> "Path | None":
    """The folder people open to read a project's journal.

    After adopt the data lives in the shared notebook, so the folder is a
    link to it; after release it links to the local journal instead. It
    is the same path the plugin used before acg, so nothing else changes.
    """
    return legacy_journal_dir(root)


def project_entry_state(root: Path, expected: Path) -> tuple[str, str]:
    """ok / missing / plain / stale / foreign / none, with a detail."""
    entry = project_entry(root)
    if entry is None:
        return "none", ""
    if entry.is_symlink() or is_reparse_point(entry):
        try:
            current = _reparse_target(entry)
        except RuntimeError as exc:
            return "foreign", str(exc)
        if _path_identity(current, expected):
            return "ok", ""
        # 指向本機日誌或共用日誌的另一邊:是 acg 自己建的,可以改指
        ours = (journal_link(root), project_journal_dir(project_key(root)))
        if any(_path_identity(current, candidate) for candidate in ours):
            return "stale", str(current)
        return "foreign", str(current)
    if entry.exists():
        return "plain", ""
    return "missing", ""


def _entry_leftovers(entry: Path) -> list[Path]:
    return [p for p in entry.iterdir() if p.name not in (".gitignore", MIGRATED_NOTE)]


def project_git_exclude(root: Path) -> "tuple[Path, str] | None":
    """The exclude file and its updated text, or None when nothing to do.

    A linked .remember would otherwise show up as untracked in the
    project's own git status; info/exclude is local and never committed.
    """
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", ".remember"],
        capture_output=True,
        check=False,
        timeout=10,
    )
    if ignored.returncode == 0:
        return None
    exclude = _git_output(root, "rev-parse", "--git-path", "info/exclude")
    if not exclude:
        return None
    path = Path(exclude)
    if not path.is_absolute():
        path = root / path
    assert_plain_path(path, directory=False)
    current = _read_text(path) if path.is_file() else ""
    if ".remember" in current.splitlines():
        return None
    body = current.rstrip("\n") + "\n" if current.strip() else ""
    return path, body + ".remember\n"


def point_project_entry(root: Path, target: Path) -> list[str]:
    """Make <project>/.remember show the journal at ``target``.

    Returns lines describing what changed; a foreign link is reported
    and left alone rather than replaced.
    """
    entry = project_entry(root)
    if entry is None:
        return []
    state, detail = project_entry_state(root, target)
    if state in ("none", "ok"):
        return []
    if state == "foreign":
        return [f"專案的 .remember 指向別處,未更動:{detail}"]
    if state == "stale":
        _remove_journal_link(entry, Path(detail))
    elif state == "plain":
        leftovers = _entry_leftovers(entry)
        if leftovers:
            names = ", ".join(p.name for p in leftovers[:5])
            raise RuntimeError(f".remember 仍有未搬移的內容:{names}")
        for name in (".gitignore", MIGRATED_NOTE):
            (entry / name).unlink(missing_ok=True)
        os.rmdir(entry)
    _create_journal_link(target, entry)
    lines = [f"專案的 .remember -> {target}"]
    exclude = project_git_exclude(root)
    if exclude is not None:
        path, text = exclude
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(path, text)
        lines.append("已寫入專案的 .git/info/exclude,git status 不會列出 .remember")
    return lines


def journal_state(root: Path) -> tuple[str, str]:
    """adopted / local / legacy / none / foreign for one project."""
    link = journal_link(root)
    key = project_key(root)
    target = project_journal_dir(key)
    if link.is_symlink() or is_reparse_point(link):
        try:
            current = _reparse_target(link)
        except OSError as exc:
            return "foreign", str(exc)
        if _path_identity(current, target):
            return "adopted", str(target)
        return "foreign", str(current)
    if link.is_dir():
        return "local", str(link)
    legacy = legacy_journal_dir(root)
    if _is_legacy_journal(legacy):
        return "legacy", str(legacy)
    return "none", ""


def adopt_journal(root: Path) -> list[str]:
    """Route this project's journal into projects/<key>/journal.

    Returns human-readable lines describing what moved. The plugin's own
    one-shot migration only fires when its target does not exist, so the
    legacy .remember content is moved here instead, the same way it does it.
    """
    state, detail = journal_state(root)
    key = project_key(root)
    target = project_journal_dir(key)
    if state == "adopted":
        # 已收編過:只補專案內的 .remember 入口(舊版搬完只留一張通知)
        return point_project_entry(root, target)
    if state == "foreign":
        raise RuntimeError(f"日誌連結已指向別處:{detail}")
    link = journal_link(root)
    lines: list[str] = []
    legacy = legacy_journal_dir(root)
    migrate_legacy = _is_legacy_journal(legacy)
    _check_journal_tree(target)
    _check_journal_tree(link)
    if migrate_legacy:
        _check_journal_tree(legacy)
    ignore = memory_dir() / ".gitignore"
    assert_plain_path(ignore, directory=False)
    moves: list[tuple[Path, Path, str]] = []
    originals: dict[Path, bytes | None] = {}
    written: dict[Path, bytes | None] = {}
    outer_observer = WRITE_OBSERVER.get()

    def observe(path: Path, content: bytes | None) -> None:
        written[path] = content
        if outer_observer is not None:
            outer_observer(path, content)

    def remember_file(path: Path) -> None:
        assert_plain_path(path, directory=False)
        originals[path] = path.read_bytes() if path.exists() else None

    observer_token = WRITE_OBSERVER.set(observe)
    try:
        target.mkdir(parents=True, exist_ok=True)
        if state == "local":
            moved = _move_contents(link, target, include_metadata=True, moves=moves)
            os.rmdir(link)
            lines.append(f"搬入 {moved} 項本機日誌")
        if migrate_legacy:
            moved = _move_contents(legacy, target, moves=moves)
            lines.append(f"搬入 {moved} 項 .remember 日誌")

        target_ignore = target / ".gitignore"
        if (
            target_ignore.exists()
            and target_ignore.read_bytes() != JOURNAL_GITIGNORE.encode()
        ):
            backup = _journal_destination(target, ".gitignore", "journal")
            moves.append((target_ignore, backup, _journal_fingerprint(target_ignore)))
            shutil.move(str(target_ignore), str(backup))
        remember_file(target_ignore)
        _write_text_atomic(target_ignore, JOURNAL_GITIGNORE)
        remember_file(ignore)
        ensure_journal_ignored()
        if migrate_legacy:
            note = legacy / MIGRATED_NOTE
            remember_file(note)
            _write_text_atomic(
                note,
                f"Memory data migrated to:\n  {target}\n"
                "This directory is now empty; you may delete it.\n",
            )
        journal_root().mkdir(parents=True, exist_ok=True)
        _create_journal_link(target, link)
    except (OSError, RuntimeError) as exc:
        errors: list[str] = []
        try:
            _remove_journal_link(link, target)
        except (OSError, RuntimeError) as restore_exc:
            errors.append(str(restore_exc))
        for path, content in reversed(list(originals.items())):
            try:
                assert_plain_path(path, directory=False)
                current = path.read_bytes() if path.exists() else None
                if current == content:
                    continue
                if path not in written or current != written[path]:
                    raise RuntimeError("日誌 metadata 已被外部修改，保留目前內容")
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            except (OSError, RuntimeError) as restore_exc:
                errors.append(f"無法還原 {path}:{restore_exc}")
        errors.extend(_restore_journal_moves(moves))
        if state == "local":
            try:
                assert_plain_path(link, directory=True)
                link.mkdir(parents=True, exist_ok=True)
            except (OSError, RuntimeError) as restore_exc:
                errors.append(str(restore_exc))
        if errors:
            raise JournalRecoveryError("日誌收編失敗;" + ";".join(errors)) from exc
        raise
    finally:
        WRITE_OBSERVER.reset(observer_token)
    lines.append(f"連結 {link.name} -> {target}")
    # 收編已完成;專案入口是給人看的,建不起來只回報,不撤銷收編
    try:
        lines.extend(point_project_entry(root, target))
    except (OSError, RuntimeError) as exc:
        lines.append(f"專案的 .remember 入口未建立:{exc}")
    return lines


def release_journal(root: Path) -> list[str]:
    """Undo adopt: the journal becomes a plain local directory again."""
    state, detail = journal_state(root)
    if state != "adopted":
        return []
    link = journal_link(root)
    target = Path(detail)
    _check_journal_tree(target)
    assert_plain_path(link.parent, directory=True)
    moves: list[tuple[Path, Path, str]] = []
    try:
        _remove_journal_link(link, target)
        moved = _move_contents(target, link, include_metadata=True, moves=moves)
    except (OSError, RuntimeError) as exc:
        errors = _restore_journal_moves(moves)
        try:
            if not (link.is_symlink() or is_reparse_point(link)):
                assert_plain_path(link, directory=True)
                if link.exists():
                    os.rmdir(link)
                _create_journal_link(target, link)
        except (OSError, RuntimeError) as restore_exc:
            errors.append(f"無法還原日誌連結 {link}:{restore_exc}")
        if errors:
            raise JournalRecoveryError("日誌移出失敗;" + ";".join(errors)) from exc
        raise
    lines = [f"日誌改回本機目錄 {link},搬回 {moved} 項"]
    try:
        lines.extend(point_project_entry(root, link))
    except (OSError, RuntimeError) as exc:
        lines.append(f"專案的 .remember 入口未更新:{exc}")
    return lines


# ─── index and git ────────────────────────────────────────────


def ensure_index() -> bool:
    """Create the notebook skeleton; return whether anything was written."""
    root = memory_dir()
    preflight_memory()
    path = index_path()
    if path.is_file():
        return False
    root.mkdir(parents=True, exist_ok=True)
    (root / TOPICS_NAME).mkdir(exist_ok=True)
    _write_text_atomic(path, INDEX_TEMPLATE)
    return True


def git_changes() -> "list[str] | None":
    """Uncommitted paths under memory/, or None when the repo is not git."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(SCRIPT_DIR),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                MEMORY_DIR_NAME,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line[3:] for line in result.stdout.splitlines() if line.strip()]


# ─── status ───────────────────────────────────────────────────


@dataclass
class MemoryStatus:
    directory: Path
    index_exists: bool
    link: str
    link_detail: str
    live_block: bool
    source_block: bool
    source_exists: bool
    agy_rules: bool
    codex_block: bool
    codex_override: bool
    changes: "list[str] | None"
    project: ProjectKey
    project_dir: Path
    project_exists: bool
    remember: bool
    journal_config: str
    journal_config_value: str
    journal: str
    journal_detail: str

    @property
    def enabled(self) -> bool:
        return self.link == "ok" and self.live_block


def inspect(cwd: "Path | None" = None) -> MemoryStatus:
    state, detail = link_state()
    key = project_key(cwd)
    project_dir = project_memory_dir(key)
    root = project_root(cwd)
    config_state, config_value = journal_config_state()
    journal, journal_detail = journal_state(root)
    return MemoryStatus(
        directory=memory_dir(),
        index_exists=index_path().is_file(),
        link=state,
        link_detail=detail,
        live_block=has_block(_read_text(live_rules_path())),
        source_block=has_block(_read_text(source_rules_path())),
        source_exists=source_rules_path().is_file(),
        agy_rules=has_block(_read_text(agy_rules_path())),
        codex_block=has_block(_read_text(codex_rules_path())),
        codex_override=codex_override_path().is_file(),
        changes=git_changes(),
        project=key,
        project_dir=project_dir,
        project_exists=(project_dir / INDEX_NAME).is_file(),
        remember=remember_installed(),
        journal_config=config_state,
        journal_config_value=config_value,
        journal=journal,
        journal_detail=journal_detail,
    )


def entry_status(path: Path) -> dict:
    """Inspect a rule entry without following unknown links or malformed blocks."""
    target = path
    try:
        target = rules_target(path)
        text = _read_text(target)
        _validate_block(text)
        installed = has_block(text)
        reason = "規則已安裝，請開新會話驗證" if installed else "尚未安裝 acg 記憶規則"
        status = "installed" if installed else "missing"
        if path == codex_rules_path() and codex_override_path().is_file():
            status = "blocked"
            reason = "AGENTS.override.md 會遮蔽共用規則"
        if path == agy_rules_path() and target.exists() and not installed:
            status, reason = "blocked", "既有 agy 規則未由 acg 管理"
    except (OSError, RuntimeError, ValueError) as exc:
        status, reason = "blocked", str(exc)
    return {
        "path": str(path),
        "target": str(target),
        "status": status,
        "reason": reason,
    }
