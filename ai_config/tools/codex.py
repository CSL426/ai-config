"""Codex CLI: config.toml filter/merge, staging projection, init, apply.
Only manages Codex-specific files; shared content is projected from claude/."""

import os
import re
import shutil
from pathlib import Path

from ..console import log_error, log_header, log_info, log_success, log_warn
from ..fsops import (
    copy_file_to_stage,
    first_existing_file,
    merge_missing_tree,
    overlay_dir_to_stage,
)
from ..paths import (
    CODEX_CANONICAL_SKILLS,
    CODEX_HOME,
    CODEX_LEGACY_SKILLS,
    CODEX_SKILLS_MIGRATION_MARKER,
    SCRIPT_DIR,
    claude_source_dir,
)
from ..safety import (
    assert_managed_paths_safe,
    assert_safe_write_target,
    codex_agents_shared_target,
    is_reparse_point,
)
from ..skills import (
    apply_managed_skills,
    project_agents_to_skills,
    reconcile_managed_skills,
    sync_shared_skills,
    sync_skills,
)

_PROJECTS_HEADER = re.compile(r"^\[projects\.")
_ANY_HEADER = re.compile(r"^\[")


def filter_codex_config(text: str) -> str:
    """Remove [projects.*] blocks; trim trailing blank lines."""
    out: list[str] = []
    skip = False
    for line in text.splitlines():
        if _PROJECTS_HEADER.match(line):
            skip = True
            continue
        if _ANY_HEADER.match(line):
            skip = False
        if not skip:
            out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def merge_codex_config(source_text: str, target_text: str) -> str:
    """Replace general settings, keep the target's [projects.*] blocks so user
    trust settings survive."""
    projects: list[str] = []
    in_projects = False
    for line in target_text.splitlines():
        if _PROJECTS_HEADER.match(line):
            in_projects = True
            projects.append(line)
            continue
        if _ANY_HEADER.match(line):
            in_projects = False
            continue
        if in_projects:
            projects.append(line)

    result = source_text.rstrip("\n")
    block = "\n".join(projects).rstrip("\n")
    if block:
        result += "\n" + block
    return result + "\n"


def stage_projection(dst: Path) -> None:
    src = SCRIPT_DIR / "codex"
    claude_src = claude_source_dir()
    dst.mkdir(parents=True, exist_ok=True)

    instruction_source = first_existing_file(src / "AGENTS.md", claude_src / "CLAUDE.md")
    if instruction_source is not None:
        copy_file_to_stage(instruction_source, dst / "AGENTS.md")

    copy_file_to_stage(src / "config.toml", dst / "config.toml")
    overlay_dir_to_stage(claude_src / "rules", dst / "rules")
    overlay_dir_to_stage(src / "rules", dst / "rules")
    project_agents_to_skills(claude_src / "agents", dst / "skills")
    if (src / "skills").is_dir():
        sync_skills(src / "skills", dst / "skills")
    if (claude_src / "skills").is_dir():
        sync_skills(claude_src / "skills", dst / "skills")
    sync_shared_skills("codex", dst / "skills")


def preflight_init() -> bool:
    src = CODEX_HOME
    dst = SCRIPT_DIR / "codex"

    if not src.is_dir():
        log_error(f"Codex config directory not found: {src}")
        return False


    assert_managed_paths_safe(src, ("config.toml",), ())
    assert_managed_paths_safe(dst, ("config.toml",), ())
    return True


def init() -> bool:
    log_header("Init Codex")
    if not preflight_init():
        return False
    src = CODEX_HOME
    dst = SCRIPT_DIR / "codex"

    if (src / "config.toml").is_file():
        dst.mkdir(parents=True, exist_ok=True)
        source_stat = (src / "config.toml").stat()
        filtered = filter_codex_config(
            (src / "config.toml").read_text(encoding="utf-8-sig")
        )
        (dst / "config.toml").write_text(filtered, encoding="utf-8", newline="\n")
        os.utime(
            dst / "config.toml",
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
        )
        log_success("config.toml (filtered, no [projects.*])")

    log_info("Skipping shared files (projected from claude/ during apply)")
    log_success("Codex init complete")
    return True


def prepare_codex_canonical_skills() -> None:
    canonical = CODEX_CANONICAL_SKILLS
    legacy = CODEX_LEGACY_SKILLS
    marker = canonical / CODEX_SKILLS_MIGRATION_MARKER
    canonical.mkdir(parents=True, exist_ok=True)
    if marker.is_file() or not legacy.is_dir() or is_reparse_point(legacy):
        return
    migrated = merge_missing_tree(legacy, canonical, "legacy Codex skills")
    assert_safe_write_target(marker)
    marker.write_text("migrated\n", encoding="utf-8", newline="\n")
    if migrated:
        log_warn(f"Migrated legacy Codex skills into: {canonical}")


def apply_internal(src: Path, dst: Path) -> None:
    prepare_codex_canonical_skills()

    if (src / "AGENTS.md").is_file():
        agents_destination = dst / "AGENTS.md"
        shared_target = codex_agents_shared_target(agents_destination)
        if shared_target is not None:
            if (SCRIPT_DIR / "codex" / "AGENTS.md").is_file():
                raise RuntimeError(
                    "Refusing to apply Codex-specific AGENTS.md through the "
                    "shared Claude instructions link"
                )
            shutil.copy2(src / "AGENTS.md", shared_target)
            log_success("AGENTS.md (shared with ~/.claude/CLAUDE.md)")
        else:
            shutil.copy2(src / "AGENTS.md", agents_destination)
            log_success("AGENTS.md")

    if (src / "config.toml").is_file():
        dst.mkdir(parents=True, exist_ok=True)
        if (dst / "config.toml").is_file():
            source_stat = (src / "config.toml").stat()
            merged = merge_codex_config(
                (src / "config.toml").read_text(encoding="utf-8"),
                (dst / "config.toml").read_text(encoding="utf-8"),
            )
            (dst / "config.toml").write_text(merged, encoding="utf-8", newline="\n")
            os.utime(
                dst / "config.toml",
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
            log_success("config.toml (merged, preserved [projects.*])")
        else:
            shutil.copy2(src / "config.toml", dst / "config.toml")
            log_success("config.toml (fresh copy)")

    # rules/ merged overlay (rsync -aL, no deletion)
    if (src / "rules").is_dir():
        overlay_dir_to_stage(src / "rules", dst / "rules")
        log_success("rules/")

    if (src / "skills").is_dir():
        apply_managed_skills(src / "skills", CODEX_CANONICAL_SKILLS)
        log_success("skills/")
    reconcile_managed_skills(src / "skills", CODEX_CANONICAL_SKILLS)
