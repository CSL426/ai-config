"""status command: diff the staged repo projection against live tool homes."""

import difflib
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from ..console import (
    GREEN,
    NC,
    RED,
    YELLOW,
    log_header,
    log_info,
    log_success,
    log_warn,
)
from ..fsops import dir_has_files, is_excluded
from ..mirrors import check_shared_mirrors
from ..paths import (
    ALL_TOOLS,
    CLAUDE_HOME,
    CLAUDE_MANAGED_DIRS,
    CODEX_CANONICAL_SKILLS,
    codex_live_skills,
    tilde,
    tool_home,
)
from ..plugins import check_plugin_drift
from ..safety import is_reparse_point
from ..skills import managed_skill_orphans, unmanaged_skills
from ..tools import agy, claude, codex
from .apply import _TOOLS


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
    if not live_dir.is_dir():
        return []
    # A managed directory the repo does not track yet still has live content to
    # report — treat a missing stage side as empty rather than skipping the tree.
    staged = (
        {
            path.relative_to(stage_dir)
            for path in stage_dir.rglob("*")
            if path.is_file()
        }
        if stage_dir.is_dir()
        else set()
    )
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
        # agy mirrors plugins/ only when the repo has it, leaving the live tree
        # alone otherwise, so reporting it as pending deletion would contradict
        # apply. Claude instead deletes a managed directory the repo dropped,
        # so there the live-only files really are going away.
        if tool == "agy" and not (stage_dir / name).is_dir():
            continue
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


_GROUP_THRESHOLD = 3


def _group_key(path: str) -> str:
    """聚合鍵:skills/acg/** → skills/acg,commands/x.md → commands。"""
    parts = path.split("/")
    if len(parts) == 1:
        return path
    if len(parts) == 2:
        return parts[0]
    return f"{parts[0]}/{parts[1]}"


def _print_new_files(entries: "list[tuple[str, Path]]") -> None:
    # 一行一檔在首次同步會列出數百行;同目錄超過門檻就收成統計行
    groups: dict[str, list[tuple[str, Path]]] = {}
    for rel_text, ai_file in entries:
        groups.setdefault(_group_key(rel_text), []).append((rel_text, ai_file))
    for key in sorted(groups):
        members = groups[key]
        if len(members) > _GROUP_THRESHOLD:
            latest = max((_latest_mtime_ns(f) or 0) for _, f in members)
            print(
                f"  {GREEN}+ {key}/{NC} ({len(members)} files only in "
                f"ai-config; repo modified {_format_mtime(latest or None)})"
            )
        else:
            for rel_text, ai_file in members:
                print(
                    f"  {GREEN}+ {rel_text}{NC} (only in ai-config; "
                    f"repo modified {_format_mtime(_latest_mtime_ns(ai_file))})"
                )


def _print_removal_entries(removals: "list[Path]", home_dir: Path) -> None:
    groups: dict[str, list[Path]] = {}
    for relative in removals:
        groups.setdefault(_group_key(relative.as_posix()), []).append(relative)
    for key in sorted(groups):
        members = groups[key]
        if len(members) > _GROUP_THRESHOLD:
            print(
                f"  {RED}- {key}/{NC} ({len(members)} files only in live; "
                "apply removes)"
            )
        else:
            for relative in members:
                live_path = home_dir / relative
                print(
                    f"  {RED}- {relative.as_posix()}{NC} "
                    "(only in live; apply removes; "
                    f"live modified {_format_mtime(_latest_mtime_ns(live_path))})"
                )


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
        new_files: list[tuple[str, Path]] = []
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
                new_files.append((rel_text, ai_file))
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

        _print_new_files(new_files)

        removals = _planned_removals(tool, stage_dir, home_dir)
        if removals:
            _print_removal_entries(removals, home_dir)
            has_diff = True

        if has_diff:
            log_info(
                "mtime is a hint only; Git checkout and copy operations can change it"
            )
        else:
            log_success("No differences found")
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

def _skill_stores(tool: str) -> list[tuple[str, Path]]:
    """Live skill directories to inspect, as (label, path) per selected tool."""
    stores: list[tuple[str, Path]] = []
    for selected_tool in ALL_TOOLS:
        if tool not in ("all", selected_tool):
            continue
        if selected_tool == "claude":
            stores.append(("claude", CLAUDE_HOME / "skills"))
        elif selected_tool == "codex":
            stores.append(("codex", codex_live_skills()))
        else:
            stores.append((selected_tool, tool_home(selected_tool) / "skills"))
    return stores


def check_unmanaged_skills(tool: str) -> None:
    found = False
    for label, store in _skill_stores(tool):
        names = unmanaged_skills(store)
        if not names:
            continue
        found = True
        log_warn(f"{label}: {len(names)} skill(s) on disk that ai-config does not manage")
        for name in names:
            print(f"    {tilde(store / name)}")
    if found:
        log_info("These are left untouched by apply; remove any you no longer want")
    else:
        log_success("No unmanaged skill directories")


def show_status(tool: str) -> None:
    for selected_tool in ALL_TOOLS:
        if tool in ("all", selected_tool):
            status_tool(selected_tool)
    log_header("Shared skill mirrors")
    check_shared_mirrors()
    log_header("Unmanaged skills")
    check_unmanaged_skills(tool)
    log_header("Plugin drift")
    check_plugin_drift()
