"""The remember plugin's per-project journal inside the shared notebook.

Where each project's journal lives, adopting it into the notebook and
releasing it back, and the <project>/.remember entry that points at it.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import memory_paths
from .links import _path_identity, _reparse_target, _try_create_junction
from .safety import is_reparse_point

REMEMBER_PLUGIN_CACHE = (
    memory_paths.CLAUDE_HOME
    / "plugins" / "cache" / "claude-plugins-official" / "remember"
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


def remember_installed() -> bool:
    return REMEMBER_PLUGIN_CACHE.is_dir()


def journal_config_state() -> tuple[str, str]:
    """ours / unset / other, plus the current value for other."""
    config = memory_paths._read_user_config()
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
    config = memory_paths._read_user_config() or {}
    config["data_dir"] = JOURNAL_TEMPLATE
    memory_paths._write_text_atomic(
        memory_paths.REMEMBER_USER_CONFIG,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    )
    return True, ""


def remove_journal_config() -> bool:
    state, _ = journal_config_state()
    if state != "ours":
        return False
    config = memory_paths._read_user_config() or {}
    config.pop("data_dir", None)
    if config:
        memory_paths._write_text_atomic(
            memory_paths.REMEMBER_USER_CONFIG,
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        )
    else:
        memory_paths._unlink_file(memory_paths.REMEMBER_USER_CONFIG)
    return True


def journal_link(root: Path) -> Path:
    return memory_paths.journal_root() / memory_paths.session_slug(root)


def project_journal_dir(key: memory_paths.ProjectKey) -> Path:
    return memory_paths.project_memory_dir(key) / memory_paths.JOURNAL_DIR_NAME


def ensure_journal_ignored() -> bool:
    """journal/<slug> links are per machine; only projects/<key>/journal syncs."""
    ignore = memory_paths.memory_dir() / ".gitignore"
    current = memory_paths._read_text(ignore)
    # 一定要錨定:沒有斜線開頭的 journal/ 會連 projects/<key>/journal/ 一起忽略
    rule = f"/{memory_paths.JOURNAL_DIR_NAME}/"
    lines = [line for line in current.splitlines() if line != f"{memory_paths.JOURNAL_DIR_NAME}/"]
    if rule in lines:
        return False
    lines.append(rule)
    memory_paths._write_text_atomic(ignore, "\n".join(lines) + "\n")
    return True


def _check_journal_tree(path: Path) -> None:
    """Reject links before moving anything, including links inside subfolders."""
    memory_paths.assert_plain_path(path, directory=True)
    if not path.exists():
        return
    for entry in path.iterdir():
        memory_paths.assert_plain_path(entry)
        if entry.is_dir():
            _check_journal_tree(entry)


def _journal_destination(destination: Path, name: str, origin: str) -> Path:
    candidate = destination / name
    suffix = 0
    while candidate.exists() or candidate.is_symlink() or is_reparse_point(candidate):
        suffix += 1
        extra = "" if suffix == 1 else f"-{suffix}"
        candidate = destination / f"{name}.from-{origin}{extra}"
    memory_paths.assert_plain_path(candidate)
    return candidate


class JournalRecoveryError(RuntimeError):
    recovery_required = True


def _journal_fingerprint(path: Path) -> str:
    """A content signature used to avoid undoing someone else's journal edit."""
    memory_paths._assert_tree(path)
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
            memory_paths.assert_plain_path(source)
            memory_paths.assert_plain_path(destination)
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
    memory_paths.assert_plain_path(link)
    if memory_paths.NATIVE_WINDOWS:
        if not _try_create_junction(target, link):
            raise RuntimeError(f"無法建立 Junction:{link}")
    else:
        link.symlink_to(target)


def _remove_journal_link(link: Path, target: Path) -> None:
    memory_paths.assert_plain_path(link.parent, directory=True)
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
    if root.resolve() == memory_paths.HOME.resolve():
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
        ours = (journal_link(root), project_journal_dir(memory_paths.project_key(root)))
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
    exclude = memory_paths._git_output(root, "rev-parse", "--git-path", "info/exclude")
    if not exclude:
        return None
    path = Path(exclude)
    if not path.is_absolute():
        path = root / path
    memory_paths.assert_plain_path(path, directory=False)
    current = memory_paths._read_text(path) if path.is_file() else ""
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
        memory_paths._write_text_atomic(path, text)
        lines.append("已寫入專案的 .git/info/exclude,git status 不會列出 .remember")
    return lines


def journal_state(root: Path) -> tuple[str, str]:
    """adopted / local / legacy / none / foreign for one project."""
    link = journal_link(root)
    key = memory_paths.project_key(root)
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
    key = memory_paths.project_key(root)
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
    ignore = memory_paths.memory_dir() / ".gitignore"
    memory_paths.assert_plain_path(ignore, directory=False)
    moves: list[tuple[Path, Path, str]] = []
    originals: dict[Path, bytes | None] = {}
    written: dict[Path, bytes | None] = {}
    outer_observer = memory_paths.WRITE_OBSERVER.get()

    def observe(path: Path, content: bytes | None) -> None:
        written[path] = content
        if outer_observer is not None:
            outer_observer(path, content)

    def remember_file(path: Path) -> None:
        memory_paths.assert_plain_path(path, directory=False)
        originals[path] = path.read_bytes() if path.exists() else None

    observer_token = memory_paths.WRITE_OBSERVER.set(observe)
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
        memory_paths._write_text_atomic(target_ignore, JOURNAL_GITIGNORE)
        remember_file(ignore)
        ensure_journal_ignored()
        if migrate_legacy:
            note = legacy / MIGRATED_NOTE
            remember_file(note)
            memory_paths._write_text_atomic(
                note,
                f"Memory data migrated to:\n  {target}\n"
                "This directory is now empty; you may delete it.\n",
            )
        memory_paths.journal_root().mkdir(parents=True, exist_ok=True)
        _create_journal_link(target, link)
    except (OSError, RuntimeError) as exc:
        errors: list[str] = []
        try:
            _remove_journal_link(link, target)
        except (OSError, RuntimeError) as restore_exc:
            errors.append(str(restore_exc))
        for path, content in reversed(list(originals.items())):
            try:
                memory_paths.assert_plain_path(path, directory=False)
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
                memory_paths.assert_plain_path(link, directory=True)
                link.mkdir(parents=True, exist_ok=True)
            except (OSError, RuntimeError) as restore_exc:
                errors.append(str(restore_exc))
        if errors:
            raise JournalRecoveryError("日誌收編失敗;" + ";".join(errors)) from exc
        raise
    finally:
        memory_paths.WRITE_OBSERVER.reset(observer_token)
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
    memory_paths.assert_plain_path(link.parent, directory=True)
    moves: list[tuple[Path, Path, str]] = []
    try:
        _remove_journal_link(link, target)
        moved = _move_contents(target, link, include_metadata=True, moves=moves)
    except (OSError, RuntimeError) as exc:
        errors = _restore_journal_moves(moves)
        try:
            if not (link.is_symlink() or is_reparse_point(link)):
                memory_paths.assert_plain_path(link, directory=True)
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
