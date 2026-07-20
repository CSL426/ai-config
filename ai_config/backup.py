"""Atomic, owned backup snapshots for apply-managed paths."""

import re
import shutil
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path

from .console import log_info, log_warn
from .fsops import mirror_dir
from .paths import (
    AGY_BACKUP_PATHS,
    AGY_CANONICAL_SKILLS,
    AGY_LEGACY_SKILLS,
    BACKUP_BASE,
    BACKUP_KEEP,
    CLAUDE_BACKUP_PATHS,
    CODEX_CANONICAL_SKILLS,
    CODEX_BACKUP_PATHS,
    CODEX_LEGACY_SKILLS,
    tool_home,
)
from .safety import assert_root_not_reparse, is_reparse_point

BACKUP_MARKER = ".ai-config-backup-owned"
BACKUP_MARKER_VALUE = "ai-config-backup-v1"
_SNAPSHOT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{9}$")
_BACKUP_PATHS = {
    "claude": CLAUDE_BACKUP_PATHS,
    "codex": CODEX_BACKUP_PATHS,
    "agy": AGY_BACKUP_PATHS,
}
_SKILLS_PATHS = {
    "codex": (CODEX_CANONICAL_SKILLS, CODEX_LEGACY_SKILLS),
    "agy": (AGY_CANONICAL_SKILLS, AGY_LEGACY_SKILLS),
}


def completed_snapshots() -> list[Path]:
    if not BACKUP_BASE.is_dir() or is_reparse_point(BACKUP_BASE):
        return []
    completed = []
    for directory in BACKUP_BASE.iterdir():
        if not directory.is_dir() or is_reparse_point(directory):
            continue
        if not _SNAPSHOT_NAME.fullmatch(directory.name):
            continue
        marker = directory / BACKUP_MARKER
        if is_reparse_point(marker) or not marker.is_file():
            continue
        if marker.read_text(encoding="utf-8").strip() == BACKUP_MARKER_VALUE:
            completed.append(directory)
    return sorted(completed, key=lambda path: path.name)


def prune_backups() -> None:
    snapshots = completed_snapshots()
    old = snapshots[:-BACKUP_KEEP] if len(snapshots) > BACKUP_KEEP else []
    for snapshot in old:
        try:
            shutil.rmtree(snapshot)
        except OSError as exc:
            log_warn(f"Could not prune owned backup snapshot: {snapshot}: {exc}")
    if old:
        log_info(f"Pruned old backups (kept newest {BACKUP_KEEP})")


def _skills_source(tool: str) -> Path:
    canonical, legacy = _SKILLS_PATHS[tool]
    return canonical if canonical.is_dir() else legacy


def _managed_sources(
    tools: Iterable[str],
    stages: Mapping[str, Path],
) -> list[tuple[str, str, Path]]:
    sources = []
    for tool in tools:
        home = tool_home(tool)
        for relative_path in _BACKUP_PATHS[tool]:
            staged_path = stages[tool] / relative_path
            reconciled_skills = tool in _SKILLS_PATHS and relative_path == "skills"
            if not staged_path.exists() and not reconciled_skills:
                continue
            if reconciled_skills:
                source = _skills_source(tool)
            else:
                source = home / relative_path
            if source.exists():
                sources.append((tool, relative_path, source))
    return sources


def _copy_source(source: Path, destination: Path, *, allow_internal_symlinks: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        mirror_dir(
            source,
            destination,
            allow_internal_symlinks=allow_internal_symlinks,
        )
    else:
        shutil.copy2(source, destination)


def create_backup(
    tools: Iterable[str],
    stages: Mapping[str, Path],
) -> "Path | None":
    sources = _managed_sources(tools, stages)
    if not sources:
        return None
    assert_root_not_reparse(BACKUP_BASE, "backup root")

    BACKUP_BASE.mkdir(parents=True, exist_ok=True)
    temporary = BACKUP_BASE / f".tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        for tool, relative_path, source in sources:
            _copy_source(
                source,
                temporary / tool / relative_path,
                allow_internal_symlinks=(tool == "agy" and relative_path == "plugins"),
            )
        (temporary / BACKUP_MARKER).write_text(
            BACKUP_MARKER_VALUE + "\n", encoding="utf-8", newline="\n"
        )

        while True:
            milliseconds = time.time_ns() // 1_000_000 % 1000
            timestamp = time.strftime("%Y-%m-%d-%H%M%S") + f"{milliseconds:03d}"
            snapshot = BACKUP_BASE / timestamp
            if not snapshot.exists():
                break
            time.sleep(0.001)
        temporary.rename(snapshot)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    log_info(f"Backed up managed files → {snapshot}")
    prune_backups()
    return snapshot
