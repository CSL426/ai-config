"""What a push shows the user before it asks: diffs, paths, commit message."""

import json

from .commands.sync import _run_repo_git
from .console import confirm as confirm_prompt
from .console import log_info
from .paths import ALL_TOOLS, SCRIPT_DIR, tilde

_DIFF_DISPLAY_LIMIT = 200
_PATH_DISPLAY_LIMIT = 20


def _print_capped(lines: "list[str]", limit: int = _PATH_DISPLAY_LIMIT) -> None:
    for line in lines[:limit]:
        print(f"  {line}")
    if len(lines) > limit:
        print(f"  … and {len(lines) - limit} more")


def _print_diff_for_review(
    full_diff: str,
    stat_args: "list[str] | None",
    review_command: str,
) -> None:
    # 學 git 的顯示習慣:先給 diffstat 摘要;完整 diff 只在夠短時全印,
    # 太長改提示查看指令。審查一致性比對仍使用完整 diff,不受顯示影響。
    if stat_args is not None:
        stat = _run_repo_git(*stat_args, "--stat", "--stat-count=20")
        if stat.returncode == 0 and stat.stdout.strip():
            print(stat.stdout.rstrip())
    lines = full_diff.count("\n")
    if lines <= _DIFF_DISPLAY_LIMIT:
        print(full_diff, end="" if full_diff.endswith("\n") else "\n")
    else:
        log_info(
            f"Diff is {lines} lines; review the full diff with: {review_command}"
        )


def _review_and_confirm_push(
    pending: str,
    staged_diff: str,
    commit_message: str,
) -> bool:
    print()
    log_info("Configuration changes to commit:")
    pending_lines = pending.rstrip().splitlines()
    _print_capped(pending_lines)
    _print_diff_for_review(
        staged_diff,
        ["diff", "--cached"],
        f"git -C {tilde(SCRIPT_DIR)} diff --cached",
    )

    print()
    log_info(f"Commit message: {commit_message}")
    return confirm_prompt("Commit and push these changes? [y/N] ")


def _joined_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _staged_json_keys(path: str) -> "set[str] | None":
    current = _run_repo_git("show", f":{path}")
    if current.returncode != 0:
        return None
    previous = _run_repo_git("show", f"HEAD:{path}")
    try:
        current_document = json.loads(current.stdout)
        previous_document = (
            json.loads(previous.stdout) if previous.returncode == 0 else {}
        )
    except json.JSONDecodeError:
        return None
    if not isinstance(current_document, dict) or not isinstance(
        previous_document,
        dict,
    ):
        return None
    keys = set(current_document).union(previous_document)
    return {
        key
        for key in keys
        if current_document.get(key) != previous_document.get(key)
    }


def _proposed_push_commit_message(
    paths: list[str], scheduled: bool = False,
) -> str:
    """The subject these changes deserve, plus who sent it.

    Every machine pushes the same subjects, so a reader had to know each
    one's scheduled minute to tell an unattended push from someone's own.
    """
    subject = _push_subject(paths)
    if not scheduled:
        return subject
    return f"{subject}\n\nScheduled-By: acg autopush on {_host_name()}"


def _host_name() -> str:
    from . import schedule_table

    return schedule_table.host_name()


def _push_subject(paths: list[str]) -> str:
    normalized = [path.replace("\\", "/") for path in paths]
    settings_tools = [
        tool
        for tool in ALL_TOOLS
        if f"{tool}/settings.json" in normalized
    ]
    if len(settings_tools) == len(normalized) and settings_tools:
        changed_keys: set[str] = set()
        for tool in settings_tools:
            keys = _staged_json_keys(f"{tool}/settings.json")
            if keys is None:
                break
            changed_keys.update(keys)
        else:
            names = _joined_names(settings_tools)
            if changed_keys == {"model"}:
                return f"chore: update {names} model settings"
            return f"chore: update {names} settings"

    shared_skills = {
        parts[3]
        for path in normalized
        if len(parts := path.split("/")) >= 5
        and parts[:2] == ["claude", "shared"]
        and parts[2] in {"both", "codex", "agy"}
    }
    if len(shared_skills) == 1 and all(
        len(parts := path.split("/")) >= 5
        and parts[:3]
        in (
            ["claude", "shared", "both"],
            ["claude", "shared", "codex"],
            ["claude", "shared", "agy"],
        )
        and parts[3] in shared_skills
        for path in normalized
    ):
        return f"chore: update {next(iter(shared_skills))} shared skill"

    changed_tools = [
        tool
        for tool in ALL_TOOLS
        if any(path.startswith(f"{tool}/") for path in normalized)
    ]
    if changed_tools:
        return f"chore: update {_joined_names(changed_tools)} configuration"
    return "chore: sync ai tool configuration"
