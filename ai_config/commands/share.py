"""Share a skill into the cross-tool shared area (claude/shared/).

Bridges the gap between skills that live only on the Claude side — either
~/.claude/skills/ or an installed plugin's cache — and the repo's
claude/shared/{both,codex,agy} projection mechanism, so one command replaces
the manual copy step.
"""

import shutil
from pathlib import Path

from ..console import log_error, log_header, log_info, log_success, log_warn
from ..paths import CLAUDE_HOME, ENTRYPOINT, SCRIPT_DIR, tilde

SHARE_TARGETS = ("both", "codex", "agy")
# 與 shared 同步機制一致:只搬這些項目
_SYNCED_ITEMS = ("SKILL.md", "examples", "references", "scripts", "agents")


def shareable_skill_names() -> "list[str]":
    names = set()
    direct = CLAUDE_HOME / "skills"
    if direct.is_dir():
        for skill_dir in direct.iterdir():
            if (skill_dir / "SKILL.md").is_file():
                names.add(skill_dir.name)
    marketplaces = CLAUDE_HOME / "plugins" / "marketplaces"
    for skill_md in marketplaces.glob("*/*/skills/*/SKILL.md"):
        names.add(skill_md.parent.name)
    return sorted(names)


def find_skill_source(name: str) -> "Path | None":
    direct = CLAUDE_HOME / "skills" / name
    if (direct / "SKILL.md").is_file():
        return direct
    marketplaces = CLAUDE_HOME / "plugins" / "marketplaces"
    for candidate in sorted(marketplaces.glob(f"*/*/skills/{name}")):
        if (candidate / "SKILL.md").is_file():
            return candidate
    return None


def _copy_items(source: Path, dest: Path) -> int:
    copied = 0
    for item in _SYNCED_ITEMS:
        src = source / item
        if src.is_file():
            shutil.copy2(src, dest / item)
            copied += 1
        elif src.is_dir():
            shutil.copytree(src, dest / item)
            copied += sum(1 for f in (dest / item).rglob("*") if f.is_file())
    return copied


def shared_copies(name: str) -> "list[tuple[str, Path]]":
    """Every shared target that currently holds a copy of this skill."""
    found = []
    for target in SHARE_TARGETS:
        candidate = SCRIPT_DIR / "claude" / "shared" / target / name
        if (candidate / "SKILL.md").is_file():
            found.append((target, candidate))
    return found


def run_unshare(name: str, target: "str | None" = None) -> int:
    """Remove the repo's shared copy, undoing `share`.

    The skill's own home in ~/.claude/skills/ is left alone: only the
    cross-tool copy goes. Because the shared tree is authoritative, the next
    apply prunes the Codex and Antigravity mirrors to match.
    """
    label = target or "both/codex/agy"
    log_header(f"Unshare skill ← claude/shared/{label}")
    if target is not None and target not in SHARE_TARGETS:
        log_error(f"Unknown target: {target} (expected both|codex|agy)")
        return 1

    copies = shared_copies(name)
    if target is not None:
        copies = [(found, path) for found, path in copies if found == target]
    if not copies:
        where = f"claude/shared/{target}/" if target else "claude/shared/"
        log_error(f"Skill is not shared: {name} (nothing in {where})")
        return 1

    for found_target, path in copies:
        shutil.rmtree(path)
        log_success(f"Removed shared copy: {tilde(path)}")
        del found_target

    source = find_skill_source(name)
    if source is not None:
        log_info(f"Left the original in place: {tilde(source)}")
    log_info(
        f"Run {ENTRYPOINT} apply to drop it from the other tools"
    )
    return 0


def run_share(name: str, target: str = "both") -> int:
    log_header(f"Share skill → claude/shared/{target}")
    if target not in SHARE_TARGETS:
        log_error(f"Unknown target: {target} (expected both|codex|agy)")
        return 1

    source = find_skill_source(name)
    if source is None:
        log_error(f"Skill not found: {name}")
        log_info(
            "Looked in ~/.claude/skills/ and "
            "~/.claude/plugins/marketplaces/*/*/skills/"
        )
        return 1

    dest = SCRIPT_DIR / "claude" / "shared" / target / name
    if dest.exists():
        log_warn(f"Replacing existing shared copy: {tilde(dest)}")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    copied = _copy_items(source, dest)
    log_success(f"Shared {name} from {tilde(source)}")
    log_info(f"→ {tilde(dest)} ({copied} files)")
    log_info(f"Run {ENTRYPOINT} apply to mirror it into the other tools")
    return 0
