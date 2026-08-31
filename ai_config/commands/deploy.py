"""Deploy managed Claude configuration into a project's own .claude directory."""

from pathlib import Path

from ..console import BOLD, CYAN, NC, log_error, log_header, log_info, log_success
from ..fsops import mirror_dir, safe_cp
from ..paths import CLAUDE_MANAGED_DIRS, CLAUDE_MANAGED_FILES, SCRIPT_DIR
from ..profiles import (
    PROFILES_NAME,
    load_profiles,
    save_profile,
    valid_profile_name,
)

# Directories deployed per child entry rather than whole, so a project can take
# just the skills it needs. The name is the menu prefix: "skills/acg".
EXPANDED_DIRS = ("skills",)


def _skill_children(source: Path, name: str) -> list[str]:
    directory = source / name
    return sorted(
        child.name
        for child in directory.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def _available_items(source: Path) -> list[tuple[str, bool]]:
    """Managed entries that exist in the repo, as (name, is_dir) in menu order.

    Entries in EXPANDED_DIRS contribute one row per child ("skills/acg")
    instead of a single all-or-nothing row.
    """
    items = [(name, False) for name in CLAUDE_MANAGED_FILES if (source / name).is_file()]
    for name in CLAUDE_MANAGED_DIRS:
        if not (source / name).is_dir():
            continue
        if name in EXPANDED_DIRS:
            items += [
                (f"{name}/{child}", True) for child in _skill_children(source, name)
            ]
        else:
            items.append((name, True))
    return items


def _describe(source: Path, name: str, is_dir: bool) -> str:
    if not is_dir:
        return "file"
    count = sum(1 for p in (source / name).rglob("*") if p.is_file())
    return f"{count} file{'s' if count != 1 else ''}"


def _parse_selection(raw: str, total: int) -> "list[int] | None":
    """Indices chosen by the user, or None when the input is unusable."""
    entry = raw.strip().lower()
    if not entry:
        return None
    if entry in {"a", "all"}:
        return list(range(total))

    chosen: set[int] = set()
    for part in entry.replace(",", " ").split():
        if "-" in part[1:]:
            start, _, end = part.partition("-")
            if not (start.isdigit() and end.isdigit()):
                return None
            lo, hi = int(start), int(end)
            if lo > hi:
                lo, hi = hi, lo
            values = range(lo, hi + 1)
        elif part.isdigit():
            values = [int(part)]
        else:
            return None
        for value in values:
            if not 1 <= value <= total:
                return None
            chosen.add(value - 1)
    return sorted(chosen) or None


def _resolve_profile(
    items: list[tuple[str, bool]], wanted: list[str]
) -> "list[int] | None":
    """Map a profile's stored names onto current menu indices."""
    by_name = {name: index for index, (name, _) in enumerate(items)}
    missing = [name for name in wanted if name not in by_name]
    if missing:
        log_error(f"Profile refers to items no longer in the repo: {', '.join(missing)}")
        return None
    return sorted(by_name[name] for name in wanted)


def _write_items(
    source: Path, destination: Path, items: list[tuple[str, bool]], selection: list[int]
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for index in selection:
        name, is_dir = items[index]
        if is_dir:
            mirror_dir(source / name, destination / name)
        else:
            safe_cp(source / name, destination / name)
        log_success(f"{name}{'/' if is_dir else ''}")


def run_deploy(
    target: "str | None",
    profile: "str | None" = None,
    save_as: "str | None" = None,
) -> int:
    source = SCRIPT_DIR / "claude"
    if not source.is_dir():
        log_error(f"No Claude configuration in the data repository: {source}")
        return 1

    project = Path(target).expanduser() if target else Path.cwd()
    if not project.is_dir():
        log_error(f"Target is not a directory: {project}")
        return 1
    project = project.resolve()

    items = _available_items(source)
    if not items:
        log_error("The data repository has no managed Claude configuration to deploy")
        return 1

    if save_as is not None and not valid_profile_name(save_as):
        log_error(f"Invalid profile name: {save_as}")
        return 1

    destination = project / ".claude"

    if profile is not None:
        profiles = load_profiles(source)
        if profile not in profiles:
            known = ", ".join(sorted(profiles)) or "(none defined)"
            log_error(f"Unknown profile: {profile}")
            log_info(f"Available profiles: {known}")
            return 1
        selection = _resolve_profile(items, profiles[profile])
        if selection is None:
            return 1
        log_header(f"Deploy profile '{profile}' to {destination}")
        _write_items(source, destination, items, selection)
        log_success(f"Deployed to {destination}")
        log_info("Project settings take precedence over the user-level configuration")
        return 0

    log_header(f"Deploy to {destination}")
    for number, (name, is_dir) in enumerate(items, start=1):
        suffix = "/" if is_dir else ""
        detail = _describe(source, name, is_dir)
        print(f"  {CYAN}{number:>2}{NC}  {name}{suffix}  ({detail})")
    print()
    print(f"  Select items: numbers (1 3), a range (1-3), or {BOLD}a{NC} for all")

    try:
        selection = _parse_selection(input("  > "), len(items))
    except EOFError:
        selection = None
    if selection is None:
        log_info("Nothing selected; deploy cancelled")
        return 0

    print()
    for index in selection:
        name, is_dir = items[index]
        print(f"  {name}{'/' if is_dir else ''} → {destination / name}")
    existing = [
        items[index][0]
        for index in selection
        if (destination / items[index][0]).exists()
    ]
    if existing:
        print()
        log_info(f"Overwrites existing: {', '.join(existing)}")

    try:
        confirmed = input("\n  Deploy these items? [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        confirmed = False
    if not confirmed:
        log_info("Cancelled; nothing was written")
        return 0

    _write_items(source, destination, items, selection)

    if save_as is not None:
        save_profile(source, save_as, [items[index][0] for index in selection])
        log_success(f"Saved profile '{save_as}' to {PROFILES_NAME}")

    log_success(f"Deployed to {destination}")
    log_info("Project settings take precedence over the user-level configuration")
    return 0
