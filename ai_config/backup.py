"""Atomic, owned backup snapshots for apply-managed paths."""

import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path

from .categories import includes, selected_paths
from .console import log_info, log_warn
from .fsops import mirror_dir
from .paths import (
    AGY_BACKUP_PATHS,
    AGY_CANONICAL_SKILLS,
    AGY_LEGACY_SKILLS,
    BACKUP_BASE,
    BACKUP_KEEP,
    CLAUDE_BACKUP_PATHS,
    CODEX_BACKUP_PATHS,
    CODEX_CANONICAL_SKILLS,
    CODEX_LEGACY_SKILLS,
    tool_home,
)
from .safety import (
    assert_root_not_reparse,
    codex_agents_shared_target,
    is_reparse_point,
)

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
    *,
    category: str = "all",
) -> list[tuple[str, str, Path]]:
    sources = []
    for tool in tools:
        home = tool_home(tool)
        for relative_path in selected_paths(_BACKUP_PATHS[tool], category):
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


def managed_destinations(
    tools: Iterable[str],
    stages: Mapping[str, Path],
    *,
    category: str = "all",
) -> list[tuple[str, str, Path]]:
    """Affected live roots, including absent paths, links, and ownership state.

    Labels are stable snapshot-relative names. Canonical skill directories own
    their manifest, migration and unmanaged-baseline markers. Legacy skill roots
    are read for migration but never changed, so they are not write destinations.
    """
    result = []
    for tool in tools:
        home = tool_home(tool)
        for relative in selected_paths(_BACKUP_PATHS[tool], category):
            if relative == "skills" and tool in _SKILLS_PATHS:
                canonical, _ = _SKILLS_PATHS[tool]
                result.append((tool, "canonical-skills", canonical))
                if tool == "agy":
                    result.append((tool, "skills", home / "skills"))
                continue
            if not (stages[tool] / relative).exists():
                continue
            destination = home / relative
            result.append((tool, relative, destination))
            if tool == "codex" and relative == "AGENTS.md":
                target = codex_agents_shared_target(destination)
                if target is not None:
                    result.append((tool, "shared-CLAUDE.md", target))
        if tool == "agy" and includes(category, "skills"):
            from .links import AGY_MARKER, AGY_STATE
            from .paths import WINDOWS_MODE

            if WINDOWS_MODE:
                for name in (AGY_MARKER, AGY_STATE):
                    result.append((tool, name, home / name))
    return result


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
    *,
    category: str = "all",
) -> "Path | None":
    tools = list(tools)
    sources = _managed_sources(tools, stages, category=category)
    destinations = managed_destinations(tools, stages, category=category)
    for tool, relative, source in destinations:
        if (
            source.exists() and not is_reparse_point(source)
            and (tool, relative, source) not in sources
        ):
            sources.append((tool, relative, source))
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
        state = []
        for tool, relative, destination in destinations:
            entry = {
                "tool": tool,
                "path": relative,
                "destination": str(destination),
                "exists": destination.exists() or is_reparse_point(destination),
            }
            if is_reparse_point(destination):
                entry["link_target"] = os.readlink(destination)
            state.append(entry)
        (temporary / "destinations.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
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
