"""Skill syncing: copy SKILL.md plus supporting directories per skill, shared
skill projection, and managed-orphan reconciliation."""

import os
import shutil
from pathlib import Path

from .console import log_info, log_warn
from .frontmatter import sanitize_skill_frontmatter
from .fsops import mirror_dir
from .paths import ACKNOWLEDGED_NAME, MANIFEST_NAME, SCRIPT_DIR


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


def unmanaged_skills(dst_skills: Path) -> list[str]:
    """Skill directories present on disk that we never deployed.

    These are left alone by design (hand-installed skills must survive an
    apply), but they are invisible otherwise: a stale copy of a skill that
    also ships as a plugin shadows it silently. Surfacing them in `status`
    makes that drift visible without changing what apply prunes.
    """
    if not dst_skills.is_dir():
        return []
    manifest = dst_skills / MANIFEST_NAME
    if not manifest.is_file():
        return []
    managed = {
        name
        for name in manifest.read_text(encoding="utf-8").splitlines()
        if name
    }
    known = acknowledged_skills(dst_skills)
    return sorted(
        entry.name
        for entry in dst_skills.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name not in managed
        and entry.name not in known
    )


def _record_baseline(dst_skills: Path, incoming: "list[str]") -> None:
    """Mark everything already here, minus what we are about to deploy."""
    existing = {
        entry.name
        for entry in dst_skills.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    }
    baseline = existing - set(incoming)
    if not baseline:
        return
    marker = dst_skills / ACKNOWLEDGED_NAME
    merged = sorted(acknowledged_skills(dst_skills) | baseline)
    marker.write_text("\n".join(merged) + "\n", encoding="utf-8", newline="\n")


def acknowledged_skills(dst_skills: Path) -> set[str]:
    """Names the user has said are the tool's own, not worth reporting."""
    marker = dst_skills / ACKNOWLEDGED_NAME
    if not marker.is_file():
        return set()
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def acknowledge_unmanaged(dst_skills: Path) -> list[str]:
    """Record every currently unmanaged skill as known, and return them.

    Deliberately a snapshot rather than a maintained list: whatever a tool
    ships today stops being reported, and anything it adds later shows up
    once so the user can decide again. Nothing here needs updating when a
    tool changes its bundled set.
    """
    names = unmanaged_skills(dst_skills)
    if not names:
        return []
    marker = dst_skills / ACKNOWLEDGED_NAME
    merged = sorted(acknowledged_skills(dst_skills) | set(names))
    marker.write_text("\n".join(merged) + "\n", encoding="utf-8", newline="\n")
    return names


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

    # 第一次接管這個目錄時,先把既有的技能記成「已知」。
    # 沒有任何欄位能分辨官方內建與使用者自寫,但「在 acg 之前就存在」
    # 是可靠的訊號:那些不是 acg 放的,使用者也早就知道它們在。
    # 這裡不能用 unmanaged_skills():它在 manifest 還不存在時回空清單。
    if not manifest.is_file():
        _record_baseline(dst_skills, current)

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
