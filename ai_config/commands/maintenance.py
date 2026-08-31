"""list / reset / package / project commands."""

from pathlib import Path

from ..backup import completed_snapshots
from ..console import (
    BOLD,
    CYAN,
    GREEN,
    NC,
    RED,
    YELLOW,
    log_error,
    log_header,
    log_info,
    log_success,
    log_warn,
)
from ..fsops import count_files
from ..package import SkillNotFoundError, available_skills, package_skill
from ..paths import (
    ALL_TOOLS,
    BACKUP_BASE,
    CLAUDE_HOME,
    CLAUDE_MANAGED_DIRS,
    CLAUDE_MANAGED_FILES,
    ENTRYPOINT,
    SCRIPT_DIR,
    claude_source_dir,
    set_claude_source_dir,
)
from ..safety import assert_managed_paths_safe, assert_root_not_reparse
from .apply import apply_tools


def do_list() -> None:
    log_header("Managed AI Tool Configs")
    print()
    for name in ALL_TOOLS:
        tool_dir = SCRIPT_DIR / name
        n = count_files(tool_dir)
        if n > 0:
            print(f"  {GREEN}●{NC} {BOLD}{name}{NC} ({n} files)")
        else:
            print(f"  {YELLOW}○{NC} {name} (0 files)")
    print()
    if BACKUP_BASE.is_dir():
        n = len(completed_snapshots())
        log_info(f"Backups: {n} completed snapshots in {BACKUP_BASE}")


def do_reset() -> bool:
    log_header("Reset ai-config")
    print()
    print(f"  This will {RED}delete all config files{NC} and leave empty directories.")
    print(f"  You can then run {CYAN}{ENTRYPOINT} init{NC} to pull your own configs.")
    print()
    try:
        confirm = input("  Are you sure? [y/N] ")
    except EOFError:
        confirm = ""
    if confirm not in ("y", "Y"):
        log_info("Cancelled")
        return True

    try:
        for tool in ALL_TOOLS:
            assert_root_not_reparse(SCRIPT_DIR / tool, "tool root")
    except RuntimeError as exc:
        log_error(str(exc))
        return False

    for tool in ALL_TOOLS:
        directory = SCRIPT_DIR / tool
        if directory.is_dir():
            for item in sorted(directory.rglob("*"), reverse=True):
                if item.is_symlink() or item.is_file():
                    item.unlink(missing_ok=True)
            log_success(f"Cleared {tool}/")

    print()
    log_success(
        f"Reset complete. Run {CYAN}{ENTRYPOINT} init{NC} to populate with your configs."
    )
    return True


def do_package(name: "str | None") -> bool:
    log_header("Package skill for Claude Desktop")
    skills = available_skills()
    if not name:
        if not skills:
            log_warn(f"No shared skills found under {SCRIPT_DIR / 'claude' / 'shared'}")
            return True
        log_info("Available skills:")
        for skill_name in skills:
            print(f"  {skill_name}")
        log_info(f"Run {ENTRYPOINT} package <skill-name> to build a ZIP")
        return True

    try:
        zip_path = package_skill(name, Path.cwd())
    except SkillNotFoundError:
        log_error(f"Skill not found in shared sources: {name}")
        if skills:
            log_info("Available skills: " + ", ".join(skills))
        return False

    log_success(f"Packaged: {zip_path}")
    log_info("Upload in Claude Desktop: Settings > Customize > Skills > + > Create skill")
    return True


def do_project(tool: str) -> bool:
    log_header("Project from ~/.claude/ → tool home dirs")
    log_info(f"Source: {CLAUDE_HOME} (live, bypassing repo)")
    print()

    if not CLAUDE_HOME.is_dir():
        log_error(f"Claude config directory not found: {CLAUDE_HOME}")
        return False
    try:
        assert_managed_paths_safe(
            CLAUDE_HOME,
            tuple(CLAUDE_MANAGED_FILES),
            tuple(CLAUDE_MANAGED_DIRS),
        )
    except RuntimeError as exc:
        log_error(str(exc))
        return False

    original = claude_source_dir()
    set_claude_source_dir(CLAUDE_HOME)
    selected = [t for t in ALL_TOOLS if t != "claude" and tool in ("all", t)]
    try:
        ok = apply_tools(selected) if selected else True
    finally:
        set_claude_source_dir(original)

    print()
    if not selected:
        log_warn(f"No tools projected (tool: {tool})")
    elif ok:
        log_success(f"Projected to: {' '.join(selected)}")
        log_info(f"Verify with: {CYAN}{ENTRYPOINT} status{NC}")
    return ok
