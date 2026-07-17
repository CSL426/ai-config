"""Skill syncing: copy SKILL.md plus supporting directories per skill, shared
skill projection, and managed-orphan reconciliation."""

import os
import shutil
from pathlib import Path

from .console import log_info, log_warn
from .frontmatter import sanitize_skill_frontmatter
from .fsops import mirror_dir
from .paths import MANIFEST_NAME, SCRIPT_DIR


def _safe_skill_name(name: str) -> bool:
    return (
        bool(name)
        and Path(name).name == name
        and name not in (".", "..")
        and "\\" not in name
        and not Path(name).is_absolute()
    )


def _write_skill_document(source: Path, destination: Path, default_name: str) -> None:
    source_stat = source.stat()
    content = source.read_text(encoding="utf-8")
    destination.write_text(
        sanitize_skill_frontmatter(content, default_name),
        encoding="utf-8",
        newline="\n",
    )
    os.utime(
        destination,
        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
    )


def sync_skills(src_skills: Path, dst_skills: Path) -> None:
    if not src_skills.is_dir():
        return
    dst_skills.mkdir(parents=True, exist_ok=True)
    for skill_dir in sorted(src_skills.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        if not _safe_skill_name(skill_dir.name):
            raise RuntimeError(f"Unsafe staged skill name: {skill_dir.name}")
        dst_skill = dst_skills / skill_dir.name
        if dst_skill.exists():
            shutil.rmtree(dst_skill)
        dst_skill.mkdir(parents=True, exist_ok=True)

        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            _write_skill_document(
                skill_md,
                dst_skill / "SKILL.md",
                skill_dir.name,
            )
        for supporting_dir in ("examples", "references", "scripts", "agents"):
            source = skill_dir / supporting_dir
            if source.is_dir():
                mirror_dir(source, dst_skill / supporting_dir)


def sync_shared_skills(tool: str, dst_skills: Path) -> None:
    """Project shared skills (claude/shared/{both,<tool>}) into a tool's skills
    dir. Source is ALWAYS the repo, never live ~/.claude/."""
    shared_root = SCRIPT_DIR / "claude" / "shared"
    if (shared_root / "both").is_dir():
        sync_skills(shared_root / "both", dst_skills)
    if (shared_root / tool).is_dir():
        sync_skills(shared_root / tool, dst_skills)


def project_agents_to_skills(agents_dir: Path, dst_skills: Path) -> None:
    if not agents_dir.is_dir():
        return
    dst_skills.mkdir(parents=True, exist_ok=True)
    for agent_file in sorted(agents_dir.glob("*.md")):
        if not agent_file.is_file():
            continue
        dst_skill = dst_skills / agent_file.stem
        dst_skill.mkdir(parents=True, exist_ok=True)
        _write_skill_document(
            agent_file,
            dst_skill / "SKILL.md",
            agent_file.stem,
        )


def _current_skill_names(staged_skills: Path) -> list[str]:
    if not staged_skills.is_dir():
        return []
    return sorted(p.name for p in staged_skills.iterdir() if p.is_dir())


def managed_skill_orphans(staged_skills: Path, dst_skills: Path) -> list[str]:
    if not dst_skills.is_dir():
        return []
    manifest = dst_skills / MANIFEST_NAME
    current = set(_current_skill_names(staged_skills))
    orphans = []
    if manifest.is_file():
        for name in manifest.read_text(encoding="utf-8").splitlines():
            if not name:
                continue
            if not _safe_skill_name(name):
                log_warn(f"Ignoring unsafe managed skill name: {name}")
                continue
            if name not in current and (dst_skills / name).is_dir():
                orphans.append(name)
    return orphans


def reconcile_managed_skills(staged_skills: Path, dst_skills: Path) -> None:
    """Prune skills we managed previously but that left the source, leaving
    hand-installed skills untouched. Manifest: <dst>/.ai-config-managed."""
    if not dst_skills.is_dir():
        return
    manifest = dst_skills / MANIFEST_NAME
    current = _current_skill_names(staged_skills)

    for name in managed_skill_orphans(staged_skills, dst_skills):
        shutil.rmtree(dst_skills / name)
        log_info(f"pruned orphan skill: {name}")

    manifest.write_text("\n".join(current) + "\n", encoding="utf-8", newline="\n")


def apply_managed_skills(staged_skills: Path, dst_skills: Path) -> None:
    if not staged_skills.is_dir():
        return
    dst_skills.mkdir(parents=True, exist_ok=True)
    for skill_dir in sorted(staged_skills.iterdir()):
        if skill_dir.is_dir() and not skill_dir.name.startswith("."):
            if not _safe_skill_name(skill_dir.name):
                raise RuntimeError(f"Unsafe staged skill name: {skill_dir.name}")
            mirror_dir(skill_dir, dst_skills / skill_dir.name)
