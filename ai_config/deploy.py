"""Deploy managed Claude configuration into a project's own .claude directory."""

from pathlib import Path

from .console import BOLD, CYAN, NC, log_error, log_header, log_info, log_success
from .fsops import mirror_dir, safe_cp
from .paths import CLAUDE_MANAGED_DIRS, CLAUDE_MANAGED_FILES, SCRIPT_DIR


def _available_items(source: Path) -> list[tuple[str, bool]]:
    """Managed entries that exist in the repo, as (name, is_dir) in menu order."""
    items = [(name, False) for name in CLAUDE_MANAGED_FILES if (source / name).is_file()]
    items += [(name, True) for name in CLAUDE_MANAGED_DIRS if (source / name).is_dir()]
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


def run_deploy(target: "str | None") -> int:
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

    log_header(f"Deploy to {project / '.claude'}")
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

    destination = project / ".claude"
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

    destination.mkdir(parents=True, exist_ok=True)
    for index in selection:
        name, is_dir = items[index]
        if is_dir:
            mirror_dir(source / name, destination / name)
        else:
            safe_cp(source / name, destination / name)
        log_success(f"{name}{'/' if is_dir else ''}")

    log_success(f"Deployed to {destination}")
    log_info("Project settings take precedence over the user-level configuration")
    return 0
