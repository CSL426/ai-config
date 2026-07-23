"""ai-config — Cross-AI tool configuration manager (Python implementation)."""

from dataclasses import dataclass
from datetime import datetime
import difflib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .backup import completed_snapshots, create_backup
from .completion import SHELLS, render_completion
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
from .package import SkillNotFoundError, available_skills, package_skill
from .paths import (
    ALL_TOOLS,
    BACKUP_BASE,
    CLAUDE_HOME,
    CLAUDE_MANAGED_DIRS,
    CLAUDE_MANAGED_FILES,
    CODEX_CANONICAL_SKILLS,
    CONFIG_ERROR,
    ENTRYPOINT,
    EXCLUDED_FILES,
    SCRIPT_DIR,
    claude_source_dir,
    codex_live_skills,
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
_GIT_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@")
_SECRET_PATTERN = re.compile(
    rb"(?:[\"']?(?:password|secret|token|api[_-]?key|api[_-]?secret|"
    rb"auth[_-]?token|access[_-]?token|private[_-]?key|database_url|"
    rb"github_token|aws_(?:access_key_id|secret_access_key|session_token)|"
    rb"stripe_(?:secret_key|api_key))[\"']?\s*[:=])|"
    rb"(?:authorization\s*[:=]\s*[\"']?bearer\s+\S+)|"
    rb"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)|"
    rb"(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    rb"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    rb"sk-(?:proj-)?[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _PushSnapshot:
    branch: str
    head: str
    upstream_remote: str
    upstream_ref: str
    upstream_commit: str


@dataclass(frozen=True)
class _PushPreflight:
    ahead: int
    has_changes: bool


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
        live_skills = (
            codex_live_skills() if tool == "codex" else home_dir / "skills"
        )
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
        if not home_dir.is_dir() and not (
            tool == "codex" and codex_live_skills().is_dir()
        ):
            log_warn(f"Tool home directory not found: {home_dir}")
            return

        has_diff = False
        for ai_file in sorted(p for p in stage_dir.rglob("*") if p.is_file()):
            rel = ai_file.relative_to(stage_dir)
            if is_excluded(rel):
                continue
            rel_text = rel.as_posix()
            if tool == "codex" and rel.parts[0] == "skills":
                home_file = CODEX_CANONICAL_SKILLS.joinpath(*rel.parts[1:])
                if not CODEX_CANONICAL_SKILLS.exists():
                    home_file = codex_live_skills().joinpath(*rel.parts[1:])
            else:
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
            elif tool == "claude" and rel_text == "settings.json":
                repo_settings = claude.shared_claude_settings(
                    ai_bytes.decode("utf-8-sig", errors="replace")
                )
                live_text = home_bytes.decode("utf-8-sig", errors="replace")
                live_settings = claude.shared_claude_settings(live_text)
                if repo_settings == live_settings:
                    continue
                filtered = claude.filter_claude_settings(live_text)
                print(
                    f"  {YELLOW}~ {rel_text}{NC} "
                    "(differs, shared settings only)"
                )
                _print_diff(ai_file, filtered, rel_text)
                _print_mtime_hint(ai_file, home_file)
                has_diff = True
            elif tool == "agy" and rel_text == "settings.json":
                repo_settings = agy.shared_agy_settings(
                    ai_bytes.decode("utf-8-sig", errors="replace")
                )
                live_text = home_bytes.decode("utf-8-sig", errors="replace")
                live_settings = agy.shared_agy_settings(live_text)
                if repo_settings == live_settings:
                    continue
                filtered = agy.filter_agy_settings(
                    live_text
                )
                print(
                    f"  {YELLOW}~ {rel_text}{NC} "
                    "(differs, shared settings only)"
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


def show_status(tool: str) -> None:
    for selected_tool in ALL_TOOLS:
        if tool in ("all", selected_tool):
            status_tool(selected_tool)
    log_header("Shared skill mirrors")
    check_shared_mirrors()
    log_header("Plugin drift")
    check_plugin_drift()


def _selected_tools(tool: str) -> list[str]:
    return [name for name in ALL_TOOLS if tool == "all" or tool == name]


def _init_tools(tool: str) -> bool:
    selected = _selected_tools(tool)
    try:
        if len(selected) > 1:
            for selected_tool in selected:
                if not _TOOLS[selected_tool].preflight_init():
                    return False
        ok = True
        for selected_tool in selected:
            ok = _TOOLS[selected_tool].init() and ok
        return ok
    except Exception as exc:
        log_error(str(exc))
        return False


def _repository_operation() -> "str | None":
    git_dir = _run_repo_git("rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        _git_failure("Reading repository metadata", git_dir)
        return "<invalid>"

    markers = (
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("BISECT_LOG", "bisect"),
        ("sequencer", "sequenced Git operation"),
    )
    git_dir_path = Path(git_dir.stdout.strip())
    if not git_dir_path.is_absolute():
        git_dir_path = SCRIPT_DIR / git_dir_path
    for marker, operation in markers:
        if (git_dir_path / marker).exists():
            return operation
    return None


def _pull_preflight() -> "tuple[int, int] | None":
    operation = _repository_operation()
    if operation is not None:
        if operation != "<invalid>":
            log_error(
                f"Data repository has a {operation} in progress; pull cancelled."
            )
        return None

    status = _run_repo_git("status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        _git_failure("Reading repository status", status)
        return None
    if status.stdout.strip():
        log_error("Data repository has uncommitted changes; pull cancelled.")
        print(status.stdout.rstrip())
        return None

    branch = _run_repo_git("symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode != 0:
        log_error("Data repository is in detached HEAD state; pull cancelled.")
        return None

    upstream = _run_repo_git(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    if upstream.returncode != 0:
        log_error("Current data repository branch has no upstream; pull cancelled.")
        return None

    fetch = _run_repo_git("fetch", "--quiet")
    if fetch.returncode != 0:
        _git_failure("Fetching repository updates", fetch)
        return None

    counts = _run_repo_git(
        "rev-list",
        "--left-right",
        "--count",
        "HEAD...@{upstream}",
    )
    if counts.returncode != 0:
        _git_failure("Comparing the local branch with its upstream", counts)
        return None
    try:
        ahead_text, behind_text = counts.stdout.split()
        return int(ahead_text), int(behind_text)
    except ValueError:
        log_error("Could not determine whether the data repository is synchronized.")
        return None


def do_sync(tool: str) -> int:
    log_header("Sync repository changes")
    try:
        counts = _pull_preflight()
        if counts is None:
            return 1
    except FileNotFoundError:
        log_error("git command not found. Please install git.")
        return 1
    except Exception as exc:
        log_error(f"Failed to synchronize repository: {exc}")
        return 1

    ahead, behind = counts
    if ahead:
        log_error(
            "Data repository is not safe to fast-forward "
            f"(ahead {ahead}, behind {behind}); pull cancelled."
        )
        if behind:
            log_info("Resolve the diverged branch manually before pulling")
        else:
            log_info(f"Run {ENTRYPOINT} push to publish the local commits")
        return 1

    if behind:
        fast_forward = _run_repo_git("merge", "--ff-only", "@{upstream}")
        if fast_forward.returncode != 0:
            _git_failure("Fast-forwarding repository updates", fast_forward)
            return 1
        log_success(
            f"Data repository fast-forwarded by {behind} "
            f"{'commit' if behind == 1 else 'commits'}"
        )
    else:
        log_success("Data repository is already up to date")

    print()
    show_status(tool)

    print()
    log_info(f"Run {ENTRYPOINT} apply to deploy")
    return 0


def _run_repo_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(SCRIPT_DIR), *args],
        capture_output=True,
        text=True,
    )


def _git_failure(action: str, result: subprocess.CompletedProcess[str]) -> None:
    detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
    detail = _GIT_URL_CREDENTIALS.sub(r"\1***@", detail)
    log_error(f"{action} failed: {detail}")


def _push_preflight(selected: list[str]) -> "_PushPreflight | None":
    operation = _repository_operation()
    if operation is not None:
        if operation != "<invalid>":
            log_error(
                f"Data repository has a {operation} in progress; push cancelled."
            )
        return None

    status = _run_repo_git("status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        _git_failure("Reading repository status", status)
        return None
    has_changes = bool(status.stdout.strip())
    if has_changes:
        staged = _staged_paths()
        if staged is None:
            return None
        if staged:
            log_error("Data repository has pre-staged changes; push cancelled:")
            for path in staged:
                print(f"  {path}")
            log_info("Unstage them before retrying so push can review the full diff")
            return None

        working = _working_paths()
        if working is None:
            return None
        outside = _paths_outside(working, selected)
        if outside:
            log_error("Uncommitted paths outside the selected tools; push cancelled:")
            for path in outside:
                print(f"  {path}")
            if len(selected) < len(ALL_TOOLS):
                log_info(
                    f"Run {ENTRYPOINT} push all if every listed path is intentional"
                )
            return None

        credentials = _credential_paths(working)
        if credentials:
            log_error("Uncommitted credential files detected; push cancelled:")
            for path in credentials:
                print(f"  {path}")
            return None

    branch = _run_repo_git("symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode != 0:
        log_error("Data repository is in detached HEAD state; push cancelled.")
        return None

    fetch = _run_repo_git("fetch", "--quiet")
    if fetch.returncode != 0:
        _git_failure("Fetching repository updates", fetch)
        return None

    upstream = _run_repo_git(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    if upstream.returncode != 0:
        log_error("Current data repository branch has no upstream; push cancelled.")
        return None

    counts = _run_repo_git(
        "rev-list",
        "--left-right",
        "--count",
        "HEAD...@{upstream}",
    )
    if counts.returncode != 0:
        _git_failure("Comparing the local branch with its upstream", counts)
        return None
    try:
        ahead, behind = (int(value) for value in counts.stdout.split())
    except ValueError:
        log_error("Could not determine whether the data repository is synchronized.")
        return None
    if behind:
        log_error(
            "Data repository is not synchronized with its upstream "
            f"(ahead {ahead}, behind {behind}); push cancelled."
        )
        if ahead:
            log_info("Resolve the diverged branch manually before pushing")
        else:
            log_info(f"Run {ENTRYPOINT} pull before pushing local configuration")
        return None
    if ahead and has_changes:
        log_error(
            "Data repository has both uncommitted changes and unpublished "
            "local commits; push cancelled."
        )
        log_info("Publish or resolve the existing commits before retrying")
        return None
    return _PushPreflight(ahead=ahead, has_changes=has_changes)


def _unstage_tools(tools: list[str]) -> bool:
    result = _run_repo_git("restore", "--staged", "--", *tools)
    if result.returncode != 0:
        _git_failure("Restoring unstaged repository changes", result)
        return False
    return True


def _paths_outside(paths: list[str], selected: list[str]) -> list[str]:
    prefixes = tuple(f"{tool}/" for tool in selected)
    return [
        path
        for path in paths
        if not path.replace("\\", "/").startswith(prefixes)
    ]


def _credential_paths(paths: list[str]) -> list[str]:
    return [
        relative
        for relative in paths
        if any(
            part in EXCLUDED_FILES
            for part in relative.replace("\\", "/").split("/")
        )
    ]


def _staged_credentials() -> list[str]:
    paths = _staged_paths()
    if paths is None:
        return ["<scan failed>"]
    return _credential_paths(paths)


def _staged_paths(*, diff_filter: "str | None" = None) -> "list[str] | None":
    args = ["diff", "--cached", "--name-only", "-z"]
    if diff_filter is not None:
        args.append(f"--diff-filter={diff_filter}")
    result = _run_repo_git(*args)
    if result.returncode != 0:
        _git_failure("Scanning staged paths", result)
        return None
    return [relative for relative in result.stdout.split("\0") if relative]


def _working_paths() -> "list[str] | None":
    tracked = _run_repo_git("diff", "--name-only", "-z", "HEAD", "--")
    untracked = _run_repo_git(
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    for action, result in (
        ("Scanning uncommitted paths", tracked),
        ("Scanning untracked paths", untracked),
    ):
        if result.returncode != 0:
            _git_failure(action, result)
            return None
    paths = set(tracked.stdout.split("\0"))
    paths.update(untracked.stdout.split("\0"))
    paths.discard("")
    return sorted(paths)


def _staged_paths_outside(selected: list[str]) -> "list[str] | None":
    paths = _staged_paths()
    if paths is None:
        return None
    return _paths_outside(paths, selected)


def _staged_secret_paths() -> "list[str] | None":
    paths = _staged_paths(diff_filter="ACMRTUXB")
    if paths is None:
        return None

    matches: list[str] = []
    for path in paths:
        result = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "show", f":{path}"],
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).decode(
                "utf-8",
                errors="replace",
            )
            text_result = subprocess.CompletedProcess(
                result.args,
                result.returncode,
                "",
                detail,
            )
            _git_failure("Scanning staged content", text_result)
            return None
        if _SECRET_PATTERN.search(result.stdout):
            matches.append(path)
    return matches


def _staged_diff() -> "str | None":
    result = _run_repo_git(
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
        "--",
    )
    if result.returncode != 0:
        _git_failure("Reading staged configuration", result)
        return None
    return result.stdout


def _staged_tree() -> "str | None":
    result = _run_repo_git("write-tree")
    if result.returncode != 0:
        _git_failure("Reading the staged configuration tree", result)
        return None
    return result.stdout.strip()


def _ahead_commits(snapshot: _PushSnapshot) -> "list[str] | None":
    result = _run_repo_git(
        "rev-list",
        "--reverse",
        f"{snapshot.upstream_commit}..{snapshot.head}",
    )
    if result.returncode != 0:
        _git_failure("Reading local commits", result)
        return None
    return result.stdout.split()


def _commit_paths(commit: str) -> "list[str] | None":
    result = _run_repo_git(
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-z",
        "-r",
        "-m",
        commit,
        "--",
    )
    if result.returncode != 0:
        _git_failure(f"Scanning local commit {commit[:12]}", result)
        return None
    return [path for path in result.stdout.split("\0") if path]


def _tree_paths(commit: str) -> "set[str] | None":
    result = _run_repo_git("ls-tree", "-r", "--name-only", "-z", commit)
    if result.returncode != 0:
        _git_failure(f"Reading local commit {commit[:12]}", result)
        return None
    return {path for path in result.stdout.split("\0") if path}


def _ahead_changed_paths(commits: list[str]) -> "list[str] | None":
    paths: set[str] = set()
    for commit in commits:
        commit_paths = _commit_paths(commit)
        if commit_paths is None:
            return None
        paths.update(commit_paths)
    return sorted(paths)


def _ahead_secret_paths(commits: list[str]) -> "list[str] | None":
    matches: set[str] = set()
    for commit in commits:
        changed = _commit_paths(commit)
        present = _tree_paths(commit)
        if changed is None or present is None:
            return None
        for path in set(changed).intersection(present):
            result = subprocess.run(
                ["git", "-C", str(SCRIPT_DIR), "show", f"{commit}:{path}"],
                capture_output=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).decode(
                    "utf-8",
                    errors="replace",
                )
                text_result = subprocess.CompletedProcess(
                    result.args,
                    result.returncode,
                    "",
                    detail,
                )
                _git_failure(f"Scanning local commit {commit[:12]}", text_result)
                return None
            if _SECRET_PATTERN.search(result.stdout):
                matches.add(path)
    return sorted(matches)


def _ahead_diff(snapshot: _PushSnapshot) -> "str | None":
    result = _run_repo_git(
        "diff",
        "--binary",
        "--no-ext-diff",
        f"{snapshot.upstream_commit}..{snapshot.head}",
        "--",
    )
    if result.returncode != 0:
        _git_failure("Reading local commit changes", result)
        return None
    return result.stdout


def _push_snapshot() -> "_PushSnapshot | None":
    branch = _run_repo_git("symbolic-ref", "--quiet", "--short", "HEAD")
    head = _run_repo_git("rev-parse", "HEAD")
    if branch.returncode != 0:
        log_error("Data repository is in detached HEAD state; push cancelled.")
        return None
    branch_name = branch.stdout.strip()
    upstream_remote = _run_repo_git(
        "config",
        "--get",
        f"branch.{branch_name}.remote",
    )
    upstream_ref = _run_repo_git(
        "config",
        "--get",
        f"branch.{branch_name}.merge",
    )
    upstream_commit = _run_repo_git("rev-parse", "@{upstream}")
    for action, result in (
        ("Reading the current data repository commit", head),
        ("Reading the current upstream remote", upstream_remote),
        ("Reading the current upstream branch", upstream_ref),
        ("Reading the current upstream commit", upstream_commit),
    ):
        if result.returncode != 0:
            _git_failure(action, result)
            return None
    return _PushSnapshot(
        branch=branch_name,
        head=head.stdout.strip(),
        upstream_remote=upstream_remote.stdout.strip(),
        upstream_ref=upstream_ref.stdout.strip(),
        upstream_commit=upstream_commit.stdout.strip(),
    )


def _validate_ahead_push(
    selected: list[str],
    commits: list[str],
    snapshot: _PushSnapshot,
) -> bool:
    merges = _run_repo_git(
        "rev-list",
        "--min-parents=2",
        f"{snapshot.upstream_commit}..{snapshot.head}",
    )
    if merges.returncode != 0:
        _git_failure("Checking local commit history", merges)
        return False
    if merges.stdout.strip():
        log_error("Local commit range contains a merge commit; push cancelled.")
        log_info("Review and publish this history manually with Git")
        return False

    paths = _ahead_changed_paths(commits)
    if paths is None:
        return False

    outside = _paths_outside(paths, selected)
    if outside:
        log_error("Local commits contain paths outside the selected tools:")
        for path in outside:
            print(f"  {path}")
        log_info(f"Run {ENTRYPOINT} push all if every listed path is intentional")
        return False

    credentials = _credential_paths(paths)
    if credentials:
        log_error("Local commits contain credential files; push cancelled:")
        for path in credentials:
            print(f"  {path}")
        return False

    secret_paths = _ahead_secret_paths(commits)
    if secret_paths is None:
        return False
    if secret_paths:
        log_error("Potential credential content exists in local commits:")
        for path in secret_paths:
            print(f"  {path}")
        return False

    check = _run_repo_git(
        "diff",
        "--check",
        f"{snapshot.upstream_commit}..{snapshot.head}",
        "--",
    )
    if check.returncode != 0:
        _git_failure("Validating local commit changes", check)
        return False
    return True


def _ahead_push_matches(
    snapshot: _PushSnapshot,
    selected: list[str],
    commits: list[str],
) -> bool:
    operation = _repository_operation()
    if operation is not None:
        log_error("Data repository Git state changed after review; push cancelled.")
        return False

    status = _run_repo_git("status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        _git_failure("Reading repository status", status)
        return False
    if status.stdout.strip():
        log_error("Data repository changed after review; push cancelled.")
        return False

    fetch = _run_repo_git("fetch", "--quiet")
    if fetch.returncode != 0:
        _git_failure("Refreshing repository updates", fetch)
        return False

    current = _push_snapshot()
    if current is None:
        return False
    if current != snapshot:
        log_error("Local commits or upstream changed after review; push cancelled.")
        return False
    current_commits = _ahead_commits(snapshot)
    if current_commits != commits:
        log_error("Local commit range changed after review; push cancelled.")
        return False
    return _validate_ahead_push(selected, commits, snapshot)


def _push_existing_commits(selected: list[str], ahead: int) -> int:
    snapshot = _push_snapshot()
    if snapshot is None:
        return 1
    commits = _ahead_commits(snapshot)
    if commits is None:
        return 1
    if len(commits) != ahead:
        log_error("Local commit count changed after preflight; push cancelled.")
        return 1
    if not _validate_ahead_push(selected, commits, snapshot):
        return 1

    committed_diff = _ahead_diff(snapshot)
    commit_list = _run_repo_git(
        "log",
        "--reverse",
        "--format=%h %s",
        f"{snapshot.upstream_commit}..{snapshot.head}",
    )
    if committed_diff is None:
        return 1
    if commit_list.returncode != 0:
        _git_failure("Reading local commit summary", commit_list)
        return 1

    print()
    log_info(
        f"Existing local {'commit' if ahead == 1 else 'commits'} to push:"
    )
    print(commit_list.stdout.rstrip())
    print(committed_diff, end="" if committed_diff.endswith("\n") else "\n")

    try:
        confirm = input("Push these existing local commits? [y/N] ")
    except EOFError:
        confirm = ""
    if confirm not in ("y", "Y"):
        log_info("Cancelled; existing local commits were not pushed")
        return 0
    if not _ahead_push_matches(snapshot, selected, commits):
        return 1

    push = _run_repo_git(
        "push",
        snapshot.upstream_remote,
        f"{snapshot.head}:{snapshot.upstream_ref}",
    )
    if push.returncode != 0:
        _git_failure("Pushing existing local commits", push)
        log_warn("Existing local commits remain available for review and retry")
        return 1
    log_success("Existing local commits pushed")
    return 0


def _validate_staged_push(selected: list[str]) -> bool:
    outside = _staged_paths_outside(selected)
    if outside is None:
        return False
    if outside:
        log_error("Staged paths outside the selected tools; push cancelled:")
        for path in outside:
            print(f"  {path}")
        return False

    credentials = _staged_credentials()
    if credentials:
        log_error("Credential files would be committed; push cancelled:")
        for path in credentials:
            print(f"  {path}")
        return False

    secret_paths = _staged_secret_paths()
    if secret_paths is None:
        return False
    if secret_paths:
        log_error("Potential credential content would be committed; push cancelled:")
        for path in secret_paths:
            print(f"  {path}")
        return False

    unstaged = _run_repo_git("diff", "--quiet")
    untracked = _run_repo_git("ls-files", "--others", "--exclude-standard")
    if (
        unstaged.returncode not in (0, 1)
        or untracked.returncode != 0
        or unstaged.returncode == 1
        or untracked.stdout.strip()
    ):
        log_error("Unexpected repository changes remain after staging; push cancelled.")
        return False

    check = _run_repo_git("diff", "--cached", "--check")
    if check.returncode != 0:
        _git_failure("Validating staged configuration", check)
        return False
    return True


def _review_and_confirm_push(
    pending: str,
    staged_diff: str,
    commit_message: str,
) -> bool:
    print()
    log_info("Configuration changes to commit:")
    print(pending.rstrip())
    print(staged_diff, end="" if staged_diff.endswith("\n") else "\n")

    print()
    log_info(f"Commit message: {commit_message}")
    try:
        confirm = input("Commit and push these changes? [y/N] ")
    except EOFError:
        confirm = ""
    if confirm not in ("y", "Y"):
        return False
    return True


def _stage_push_changes(selected: list[str]) -> "str | None":
    stage = _run_repo_git("add", "-A", "--", *selected)
    if stage.returncode != 0:
        _git_failure("Staging collected configuration", stage)
        return None

    if not _validate_staged_push(selected):
        _unstage_tools(selected)
        return None

    staged_diff = _staged_diff()
    if not staged_diff:
        _unstage_tools(selected)
        log_error("No staged configuration changes were found; push cancelled.")
        return None
    return staged_diff


def _staged_push_matches(selected: list[str], reviewed_diff: str) -> bool:
    if not _validate_staged_push(selected):
        return False
    current_diff = _staged_diff()
    if current_diff is None:
        return False
    if current_diff != reviewed_diff:
        log_error("Staged configuration changed after review; push cancelled.")
        return False
    return True


def _commit_and_push(
    commit_message: str,
    selected: list[str],
    reviewed_diff: str,
) -> int:
    expected_tree = _staged_tree()
    current_diff = _staged_diff()
    if expected_tree is None or current_diff is None:
        _unstage_tools(selected)
        return 1
    if current_diff != reviewed_diff:
        _unstage_tools(selected)
        log_error("Staged configuration changed before commit; push cancelled.")
        return 1

    parent = _run_repo_git("rev-parse", "HEAD")
    if parent.returncode != 0:
        _git_failure("Reading the current data repository commit", parent)
        _unstage_tools(selected)
        return 1

    commit = _run_repo_git("commit", "-m", commit_message)
    if commit.returncode != 0:
        _git_failure("Committing configuration", commit)
        return 1

    head = _run_repo_git("rev-parse", "HEAD")
    committed_tree = _run_repo_git("rev-parse", "HEAD^{tree}")
    if (
        head.returncode != 0
        or committed_tree.returncode != 0
        or committed_tree.stdout.strip() != expected_tree
    ):
        log_error("Committed configuration differed from the reviewed snapshot.")
        if head.returncode == 0:
            rollback = _run_repo_git(
                "update-ref",
                "-m",
                "reset: reject unreviewed ai-config push",
                "HEAD",
                parent.stdout.strip(),
                head.stdout.strip(),
            )
            if rollback.returncode == 0:
                _unstage_tools(selected)
                log_warn("The unreviewed local commit was rolled back and not pushed")
            else:
                _git_failure("Rolling back the unreviewed local commit", rollback)
                log_warn(
                    f"Local commit {head.stdout.strip()} was created but not pushed"
                )
        return 1

    commit_output = commit.stdout.strip()
    if commit_output:
        print(commit_output)

    push = _run_repo_git("push")
    if push.returncode != 0:
        _git_failure("Pushing configuration", push)
        head = _run_repo_git("rev-parse", "--short", "HEAD")
        if head.returncode == 0:
            log_warn(f"Local commit {head.stdout.strip()} was created but not pushed")
        return 1
    log_success("Local configuration committed and pushed")
    return 0


def do_push(tool: str) -> int:
    log_header("Push local configuration")
    selected = _selected_tools(tool)
    try:
        preflight = _push_preflight(selected)
        if preflight is None:
            return 1
    except FileNotFoundError:
        log_error("git command not found. Please install git.")
        return 1
    except Exception as exc:
        log_error(f"Failed to prepare repository push: {exc}")
        return 1

    if preflight.ahead:
        return _push_existing_commits(selected, preflight.ahead)

    if preflight.has_changes:
        log_info("Reviewing existing uncommitted configuration changes")
    elif not _init_tools(tool):
        return 1

    status = _run_repo_git("status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        _git_failure("Reading collected configuration changes", status)
        return 1
    pending = status.stdout
    if not pending.strip():
        log_success("No local configuration changes to push")
        return 0

    commit_scope = "AI tool" if tool == "all" else tool
    commit_message = f"chore: sync {commit_scope} configuration"
    reviewed_diff = _stage_push_changes(selected)
    if reviewed_diff is None:
        return 1

    confirmed = False
    ready_to_commit = False
    cleanup_succeeded = True
    try:
        confirmed = _review_and_confirm_push(
            pending,
            reviewed_diff,
            commit_message,
        )
        if confirmed:
            ready_to_commit = _staged_push_matches(selected, reviewed_diff)
    finally:
        if not ready_to_commit:
            cleanup_succeeded = _unstage_tools(selected)

    if not confirmed:
        if cleanup_succeeded:
            log_info("Cancelled; configuration changes remain unstaged")
            return 0
        log_error("Cancellation failed to restore the staged configuration.")
        return 1
    if not ready_to_commit:
        return 1
    return _commit_and_push(commit_message, selected, reviewed_diff)


# ─── main ─────────────────────────────────────────────────────


def usage() -> None:
    print(f"{BOLD}{ENTRYPOINT}{NC} — Cross-AI tool configuration manager")
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
    print("  pull [tool]     Safely fast-forward repo changes, then show status")
    print("  push [tool]     Gather, review, commit, and push local configuration")
    print("  sync [tool]     Alias for pull")
    print("  list            List managed tools")
    print("  package [skill] Zip a shared skill for Claude Desktop upload")
    print("  reset           Delete all managed config files")
    print("  completion      Print Bash or PowerShell completion script")
    print("  update          Download and install the latest release")
    print("  version         Show the installed version")
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
    if cmd == "update":
        if len(args) != 1:
            log_error(f"Usage: {ENTRYPOINT} update")
            return 1
        from .update import run_update

        return run_update()
    if cmd in ("version", "--version", "-V"):
        if len(args) != 1:
            log_error(f"Usage: {ENTRYPOINT} version")
            return 1
        from .version import current_version

        installed_version = current_version()
        if installed_version is None:
            log_error("Could not determine the installed ai-config version")
            return 1
        print(f"ai-config (acg) {installed_version}")
        return 0
    if cmd == "completion":
        if len(args) != 2 or args[1] not in SHELLS:
            log_error(f"Usage: {ENTRYPOINT} completion <bash|powershell>")
            return 1
        print(render_completion(args[1]), end="")
        return 0

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

    if cmd == "package":
        if len(args) > 2:
            log_error(f"Unexpected arguments: {' '.join(args[2:])}")
            return 1
        skill_name = args[1] if len(args) > 1 else None
        return 0 if do_package(skill_name) else 1

    if cmd in ("list", "reset") and len(args) > 1:
        log_error(f"Unexpected arguments: {' '.join(args[1:])}")
        return 1

    tool = "all"
    if len(args) > 1:
        tool = args[1]
    if len(args) > 2:
        log_error(f"Unexpected arguments: {' '.join(args[2:])}")
        return 1
    tool = resolve_tool(tool)

    if cmd == "init":
        if not _init_tools(tool):
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
    elif cmd in ("pull", "sync"):
        code = do_sync(tool)
        if code != 0:
            return code
    elif cmd == "push":
        code = do_push(tool)
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
