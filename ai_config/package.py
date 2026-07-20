"""Package a shared skill as a ZIP for manual upload to Claude Desktop.

Claude Desktop has no writable local skills directory; custom skills are
uploaded as a ZIP through Settings > Customize > Skills. This module finds a
skill under claude/shared/{both,agy,codex} and zips it in that format (the
skill directory itself at the ZIP root, containing SKILL.md).
"""

import zipfile
from pathlib import Path

from .paths import SCRIPT_DIR

SHARED_SOURCES = ("both", "agy", "codex")


class SkillNotFoundError(Exception):
    pass


def _shared_root() -> Path:
    return SCRIPT_DIR / "claude" / "shared"


def available_skills() -> list[str]:
    names = set()
    shared_root = _shared_root()
    for source in SHARED_SOURCES:
        source_dir = shared_root / source
        if not source_dir.is_dir():
            continue
        for skill_dir in source_dir.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                names.add(skill_dir.name)
    return sorted(names)


def find_skill_dir(name: str) -> Path:
    shared_root = _shared_root()
    for source in SHARED_SOURCES:
        candidate = shared_root / source / name
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            return candidate
    raise SkillNotFoundError(name)


def package_skill(name: str, output_dir: Path) -> Path:
    """Zip the skill directory for Claude Desktop upload; returns the ZIP path."""
    skill_dir = find_skill_dir(name)
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{name}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(skill_dir.rglob("*")):
            if file_path.is_file():
                arcname = Path(name) / file_path.relative_to(skill_dir)
                archive.write(file_path, arcname.as_posix())

    return zip_path
