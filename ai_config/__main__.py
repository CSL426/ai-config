"""ai-config — Cross-AI tool configuration manager (Python implementation)."""

import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .backup import completed_snapshots, create_backup
from .console import (
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
from .fsops import count_files, dir_has_files, is_excluded
from .links import preflight_windows_links
from .locking import apply_lock
from .mirrors import check_shared_mirrors
from .paths import (
    ALL_TOOLS,
    BACKUP_BASE,
    CLAUDE_HOME,
    CLAUDE_MANAGED_DIRS,
    CLAUDE_MANAGED_FILES,
    CONFIG_ERROR,
    ENTRYPOINT,
    SCRIPT_DIR,
    claude_source_dir,
    set_claude_source_dir,
    tool_home,
)
from .plugins import check_plugin_drift
from .safety import (
    assert_managed_paths_safe,
    assert_root_not_reparse,
    assert_tool_destinations_safe,
    is_reparse_point,
)
from .skills import managed_skill_orphans
from .staging import staged_projections
from .tools import agy, claude, codex

_TOOLS = {"claude": claude, "codex": codex, "agy": agy}
_HEADERS = {"claude": "Claude", "codex": "Codex", "agy": "Antigravity CLI"}


# ─── apply ────────────────────────────────────────────────────


def apply_tools(tools: list[str]) -> bool:
    snapshot = None
    try:
        with staged_projections(tools, _TOOLS, _HEADERS) as stages:
            assert_tool_destinations_safe(tools, stages)
            preflight_windows_links(tools)
            with apply_lock():
                snapshot = create_backup(tools, stages)
                for tool in tools:
                    home_dir = tool_home(tool)
                    home_dir.mkdir(parents=True, exist_ok=True)
                    _TOOLS[tool].apply_internal(stages[tool], home_dir)
    except Exception as exc:
        log_error(f"Failed to apply config: {exc}")
        if snapshot is not None:
            log_warn(
                "Live config may be partially updated. "
                f"Restore from backup if needed: {snapshot}"
            )
        return False
    return True


def apply_tool(tool: str) -> bool:
    return apply_tools([tool])


# ─── status ───────────────────────────────────────────────────


def _print_diff(ai_file: Path, home_text: str, rel: str) -> None:
    ai_text = ai_file.read_text(encoding="utf-8", errors="replace")
    diff_lines = list(
        difflib.unified_diff(
            ai_text.splitlines(),
            home_text.splitlines(),
            fromfile=f"ai-config/{rel}",
            tofile=f"live/{rel}",
            lineterm="",
        )
    )
    for line in diff_lines[:20]:
        if line.startswith("-"):
            print(f"{RED}{line}{NC}")
        elif line.startswith("+"):
            print(f"{GREEN}{line}{NC}")
        else:
            print(line)


def _latest_mtime_ns(path: Path) -> "int | None":
    try:
        latest = path.stat().st_mtime_ns
    except OSError:
        return None
    if not path.is_dir() or is_reparse_point(path):
        return latest
    for child in path.rglob("*"):
        if is_reparse_point(child):
            continue
        try:
            latest = max(latest, child.stat().st_mtime_ns)
        except OSError:
            continue
    return latest


def _format_mtime(value: "int | None") -> str:
    if value is None:
        return "unknown"
    timestamp = value / 1_000_000_000
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _print_mtime_hint(repo_path: Path, live_path: Path) -> None:
    repo_mtime = _latest_mtime_ns(repo_path)
    live_mtime = _latest_mtime_ns(live_path)
    if repo_mtime is None or live_mtime is None:
        newer = "unknown"
    elif abs(repo_mtime - live_mtime) <= 1_000_000_000:
        newer = "timestamps effectively equal"
    elif repo_mtime > live_mtime:
        newer = "repo newer"
    else:
        newer = "live newer"
    print(
        f"    mtime hint: {newer}; repo {_format_mtime(repo_mtime)}; "
        f"live {_format_mtime(live_mtime)}"
    )


def _mirror_live_only_files(stage_dir: Path, live_dir: Path) -> list[Path]:
    if not stage_dir.is_dir() or not live_dir.is_dir():
        return []
    staged = {
        path.relative_to(stage_dir)
        for path in stage_dir.rglob("*")
        if path.is_file()
    }
    removals = []
    for path in live_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(live_dir)
        if any(is_excluded(part) for part in relative.parts):
            continue
        if relative not in staged:
            removals.append(relative)
    return sorted(removals)


def _planned_removals(tool: str, stage_dir: Path, home_dir: Path) -> list[Path]:
    removals = []
    if tool == "claude":
        exact_mirrors = CLAUDE_MANAGED_DIRS
    elif tool == "agy":
        exact_mirrors = ["plugins"]
    else:
        exact_mirrors = []
    for name in exact_mirrors:
        removals.extend(
            Path(name) / relative
            for relative in _mirror_live_only_files(
                stage_dir / name, home_dir / name
            )
        )

    if tool in ("codex", "agy"):
        staged_skills = stage_dir / "skills"
        live_skills = home_dir / "skills"
        if staged_skills.is_dir():
            for skill in staged_skills.iterdir():
                if not skill.is_dir() or skill.name.startswith("."):
                    continue
                removals.extend(
                    Path("skills") / skill.name / relative
                    for relative in _mirror_live_only_files(
                        skill, live_skills / skill.name
                    )
                )
        removals.extend(
            Path("skills") / name
            for name in managed_skill_orphans(staged_skills, live_skills)
        )
    return sorted(set(removals))


def status_tool(tool: str) -> None:
    module = _TOOLS[tool]
    home_dir = tool_home(tool)
    stage_dir = Path(tempfile.mkdtemp())
    try:
        module.stage_projection(stage_dir)
        log_header(f"Status: {tool}")

        if not dir_has_files(stage_dir):
            log_warn(f"No config in ai-config/{tool}/")
            return
        if not home_dir.is_dir():
            log_warn(f"Tool home directory not found: {home_dir}")
            return

        has_diff = False
        for ai_file in sorted(p for p in stage_dir.rglob("*") if p.is_file()):
            rel = ai_file.relative_to(stage_dir)
            if is_excluded(rel):
                continue
            rel_text = rel.as_posix()
            home_file = home_dir / rel

            if not home_file.is_file():
                print(
                    f"  {GREEN}+ {rel_text}{NC} (only in ai-config; "
                    f"repo modified {_format_mtime(_latest_mtime_ns(ai_file))})"
                )
                has_diff = True
                continue

            ai_bytes = ai_file.read_bytes()
            home_bytes = home_file.read_bytes()
            if ai_bytes == home_bytes:
                continue

            if tool == "codex" and rel_text == "config.toml":
                repo_config = codex.filter_codex_config(
                    ai_bytes.decode("utf-8", errors="replace")
                )
                filtered = codex.filter_codex_config(
                    home_bytes.decode("utf-8", errors="replace")
                )
                if repo_config == filtered:
                    continue
                print(
                    f"  {YELLOW}~ {rel_text}{NC} "
                    "(differs, general settings only)"
                )
                _print_diff(ai_file, filtered, rel_text)
                _print_mtime_hint(ai_file, home_file)
                has_diff = True
            else:
                print(f"  {YELLOW}~ {rel_text}{NC}")
                _print_diff(
                    ai_file,
                    home_bytes.decode("utf-8", errors="replace"),
                    rel_text,
                )
                _print_mtime_hint(ai_file, home_file)
                has_diff = True

        for relative in _planned_removals(tool, stage_dir, home_dir):
            live_path = home_dir / relative
            print(
                f"  {RED}- {relative.as_posix()}{NC} "
                "(only in live; apply removes; "
                f"live modified {_format_mtime(_latest_mtime_ns(live_path))})"
            )
            has_diff = True

        if has_diff:
            log_info(
                "mtime is a hint only; Git checkout and copy operations can change it"
            )
        else:
            log_success("No differences found")
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


# ─── list / reset / project ───────────────────────────────────


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


def show_status(tool: str) -> None:
    for selected_tool in ALL_TOOLS:
        if tool in ("all", selected_tool):
            status_tool(selected_tool)
    log_header("Shared skill mirrors")
    check_shared_mirrors()
    log_header("Plugin drift")
    check_plugin_drift()


def do_sync(tool: str) -> int:
    log_header("Sync repository changes")
    try:
        result = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "pull", "--rebase", "--autostash"]
        )
        if result.returncode != 0:
            return result.returncode
    except FileNotFoundError:
        log_error("git command not found. Please install git.")
        return 1
    except Exception as exc:
        log_error(f"Failed to execute git pull: {exc}")
        return 1

    print()
    show_status(tool)

    print()
    log_info(f"Run {ENTRYPOINT} apply to deploy")
    return 0


# ─── main ─────────────────────────────────────────────────────


def usage() -> None:
    print(f"{BOLD}ai-config{NC} — Cross-AI tool configuration manager")
    print()
    print(f"{BOLD}Usage:{NC}")
    print(f"  {ENTRYPOINT} <command> [tool]")
    print()
    print(f"{BOLD}Commands:{NC}")
    print("  setup           Configure data repository and verify push access")
    print("  init [tool]     Gather configs from tool homes into the data repository")
    print("  apply [tool]    Deploy data repository configs to tool home directories")
    print("  project [tool]  Project ~/.claude/ directly to other tool home dirs")
    print("  status [tool]   Show diff between the data repository and live configs")
    print("  sync [tool]     Pull latest repo changes, then show status")
    print("  list            List managed tools")
    print("  reset           Delete all managed config files")
    print("  help            Show this help")
    print()
    print(f"{BOLD}Tools:{NC}")
    print("  claude          Claude Code (~/.claude/)")
    print("  codex           Codex CLI (~/.codex/)")
    print("  agy             Antigravity CLI (~/.gemini/antigravity-cli/)")
    print("  all             All supported tools (default)")


def resolve_tool(tool: str) -> str:
    aliases = {"antigravity": "agy", "antigravity-cli": "agy", "antigravity_cli": "agy"}
    tool = aliases.get(tool, tool)
    if tool not in ("claude", "codex", "agy", "all"):
        log_error(f"Unknown tool: {tool}")
        sys.exit(1)
    return tool


def main(argv: "list[str] | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        if (
            "PYTEST_CURRENT_TEST" not in os.environ
            and not (SCRIPT_DIR / "claude").is_dir()
            and sys.stdin.isatty()
        ):
            from .setup import run_setup

            return run_setup([])
        usage()
        return 0

    cmd = args[0]
    if cmd in ("help", "--help", "-h"):
        if len(args) > 1:
            log_error(f"Unexpected arguments: {' '.join(args[1:])}")
            return 1
        usage()
        return 0
    if cmd == "setup":
        from .setup import run_setup

        return run_setup(args[1:])

    if CONFIG_ERROR:
        log_error(CONFIG_ERROR)
        log_info(f"Run {ENTRYPOINT} setup to replace the invalid configuration")
        return 1
    if "PYTEST_CURRENT_TEST" not in os.environ and not (SCRIPT_DIR / "claude").is_dir():
        log_error(
            f"Repository configuration directory not found at {SCRIPT_DIR}.\n"
            f"Run {ENTRYPOINT} setup to configure and verify your data repository."
        )
        return 1

    tool = "all"
    if len(args) > 1:
        tool = args[1]
    if len(args) > 2:
        log_error(f"Unexpected arguments: {' '.join(args[2:])}")
        return 1
    tool = resolve_tool(tool)

    if cmd == "init":
        ok = True
        selected = [t for t in ALL_TOOLS if tool in ("all", t)]
        try:
            if len(selected) > 1:
                for t in selected:
                    if not _TOOLS[t].preflight_init():
                        return 1
            for t in selected:
                ok = _TOOLS[t].init() and ok
        except Exception as exc:
            log_error(str(exc))
            return 1
        if not ok:
            return 1
        print()
        log_success(f"Init complete. Review with: {CYAN}{ENTRYPOINT} status{NC}")
    elif cmd == "apply":
        selected = [t for t in ALL_TOOLS if tool in ("all", t)]
        if not apply_tools(selected):
            return 1
        print()
        log_success(f"Apply complete. Verify with: {CYAN}{ENTRYPOINT} status{NC}")
    elif cmd == "project":
        if not do_project(tool):
            return 1
    elif cmd == "sync":
        code = do_sync(tool)
        if code != 0:
            return code
    elif cmd == "status":
        show_status(tool)
    elif cmd == "list":
        do_list()
    elif cmd == "reset":
        if not do_reset():
            return 1
    else:
        log_error(f"Unknown command: {cmd}")
        print()
        usage()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
