"""pull / sync command and the shared data-repo Git helpers."""

import re
import subprocess
from pathlib import Path

from ..console import log_error, log_header, log_info, log_success
from ..paths import ENTRYPOINT, SCRIPT_DIR
from .status import show_status

_GIT_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@")


def _repository_operation(repo_dir: "Path | None" = None) -> "str | None":
    git_dir = _run_repo_git("rev-parse", "--git-dir", repo_dir=repo_dir)
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
        git_dir_path = (repo_dir or SCRIPT_DIR) / git_dir_path
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
    from ..config import configured_remote_provider

    try:
        if configured_remote_provider() == "gdrive":
            from ..gdrive import gdrive_pull

            return gdrive_pull(SCRIPT_DIR, tool)

        log_header("Sync repository changes")
        counts = _pull_preflight()
        if counts is None:
            return 1
    except FileNotFoundError:
        log_error("git command not found. Please install git.")
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level guard must not crash
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

def _run_repo_git(
    *args: str,
    repo_dir: "Path | None" = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir or SCRIPT_DIR), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _remote_is_read_only() -> bool:
    """Whether the remote refuses writes, checked without changing it.

    A dry-run push asks the server for a decision but sends no objects and
    creates no ref, so this is safe to run before every push.
    """
    result = _run_repo_git(
        "push", "--dry-run", "--porcelain", "origin", "HEAD:refs/heads/main"
    )
    if result.returncode == 0:
        return False
    detail = (result.stderr + result.stdout).lower()
    return any(
        marker in detail
        for marker in ("denied", "permission", "403", "unauthorized", "read-only")
    )


def _git_failure(action: str, result: subprocess.CompletedProcess[str]) -> None:
    detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
    detail = _GIT_URL_CREDENTIALS.sub(r"\1***@", detail)
    log_error(f"{action} failed: {detail}")
