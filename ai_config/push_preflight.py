"""Push checks that run before anything is staged or shown.

Scope, working tree, upstream position, credential files and credential
content: everything that decides whether a push may go ahead at all.
"""

import subprocess
from dataclasses import dataclass

from . import push_review
from .commands.apply import _selected_tools
from .commands.sync import (
    _git_failure,
    _hint_remote_access,
    _repository_operation,
    _run_repo_git,
)
from .config import configured_remote_provider
from .console import log_error, log_info, log_warn
from .paths import (
    ENTRYPOINT,
    EXCLUDED_FILES,
    MEMORY_DIR_NAME,
    SCRIPT_DIR,
)
from .safety import SECRET_PATTERN as _SECRET_PATTERN

_ALLOW_SECRET_PATHS = False


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


# 記憶不是工具,但在 push 裡是一個可選範圍:memory/ 是資料 repo 的普通追蹤目錄
MEMORY_SCOPE = MEMORY_DIR_NAME


def _push_scopes(tool: str) -> list[str]:
    if tool == MEMORY_SCOPE:
        return [MEMORY_SCOPE]
    scopes = _selected_tools(tool)
    if tool == "all":
        scopes.append(MEMORY_SCOPE)
    return scopes


def _only_memory_changes() -> bool:
    working = _working_paths()
    return bool(working) and all(
        path.startswith(f"{MEMORY_SCOPE}/") for path in working
    )


def _changed_scopes(selected: list[str], paths: list[str]) -> list[str]:
    return [
        scope
        for scope in selected
        if any(path == scope or path.startswith(f"{scope}/") for path in paths)
    ]


def _memory_root_available(selected: list[str], paths: list[str]) -> bool:
    if (
        MEMORY_SCOPE in _changed_scopes(selected, paths)
        and not (SCRIPT_DIR / MEMORY_DIR_NAME).is_dir()
    ):
        log_error(
            "Memory directory is missing or is not a directory; push cancelled."
        )
        log_info("Restore memory/ before pushing; a missing root is not a deletion request")
        return False
    return True


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
            push_review._print_capped(staged)
            log_info("Unstage them before retrying so push can review the full diff")
            return None

        working = _working_paths()
        if working is None:
            return None
        if not _memory_root_available(selected, working):
            return None
        outside = _paths_outside(working, selected)
        if outside and selected == [MEMORY_SCOPE]:
            # 記憶是自己一個範圍,而且多半由排程推。設定目錄有什麼改動與它無關,
            # 拿那些擋下來只會讓沒人看著的排程整晚不跑 — 昨晚就是這樣失敗的。
            # 其他範圍仍然要擋:那是人在選,漏掉一塊值得提醒。
            log_info(f"這次只推 {MEMORY_SCOPE},其他 {len(outside)} 個未提交的改動不動它")
            outside = []
        if outside:
            log_error("Uncommitted paths outside the selected tools; push cancelled:")
            push_review._print_capped(outside)
            if all(path.startswith(f"{MEMORY_SCOPE}/") for path in outside):
                log_info(
                    f"Run {ENTRYPOINT} memory push first, or {ENTRYPOINT} push all"
                )
            elif MEMORY_SCOPE not in selected:
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

    if configured_remote_provider() == "gdrive":
        from .gdrive import GDriveClient

        if branch.stdout.strip() != "main":
            log_error("Google Drive data repository must use the main branch.")
            return None

        client = GDriveClient()
        head_info = client.get_head_info()

        local_head_result = _run_repo_git("rev-parse", "--verify", "HEAD")
        local_head = (
            local_head_result.stdout.strip()
            if local_head_result.returncode == 0
            else ""
        )
        ahead = 0
        if head_info and "commit" in head_info:
            if not local_head:
                log_error(
                    "Google Drive contains repository history but the local "
                    "repository has no commits; pull first."
                )
                return None
            remote_commit = head_info["commit"]
            if remote_commit != local_head:
                ancestor_check = _run_repo_git(
                    "merge-base",
                    "--is-ancestor",
                    remote_commit,
                    local_head,
                )
                if ancestor_check.returncode != 0:
                    log_error(
                        "Data repository is not synchronized with Google Drive "
                        "(diverged); push cancelled."
                    )
                    log_info(f"Run {ENTRYPOINT} pull before pushing local configuration")
                    return None

                ahead_count = _run_repo_git(
                    "rev-list",
                    "--count",
                    f"{remote_commit}..{local_head}",
                )
                if ahead_count.returncode == 0:
                    try:
                        ahead = int(ahead_count.stdout.strip())
                    except ValueError:
                        ahead = 0
        elif local_head:
            ahead_count = _run_repo_git("rev-list", "--count", local_head)
            if ahead_count.returncode != 0:
                _git_failure("Counting unpublished local commits", ahead_count)
                return None
            try:
                ahead = int(ahead_count.stdout.strip())
            except ValueError:
                log_error("Could not count unpublished local commits.")
                return None

        if ahead and has_changes:
            log_error(
                "Data repository has both uncommitted changes and unpublished "
                "local commits; push cancelled."
            )
            log_info("Publish or resolve the existing commits before retrying")
            return None
        return _PushPreflight(ahead=ahead, has_changes=has_changes)

    fetch = _run_repo_git("fetch", "--quiet")
    if fetch.returncode != 0:
        _git_failure("Fetching repository updates", fetch)
        _hint_remote_access(fetch)
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
    head = _run_repo_git("rev-parse", "--verify", "--quiet", "HEAD")
    if head.returncode == 0:
        tracked_runs = [_run_repo_git("diff", "--name-only", "-z", "HEAD", "--")]
    else:
        # 全新 repo 的 unborn HEAD 沒有比較基準(gdrive 首次 push 會遇到):
        # 改以「索引 vs 空樹」(--cached)加「工作樹 vs 索引」涵蓋所有未提交內容。
        tracked_runs = [
            _run_repo_git("diff", "--cached", "--name-only", "-z", "--"),
            _run_repo_git("diff", "--name-only", "-z", "--"),
        ]
    untracked = _run_repo_git(
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    checks = [("Scanning uncommitted paths", run) for run in tracked_runs]
    checks.append(("Scanning untracked paths", untracked))
    for action, result in checks:
        if result.returncode != 0:
            _git_failure(action, result)
            return None
    paths: set[str] = set()
    for run in tracked_runs:
        paths.update(run.stdout.split("\0"))
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
            check=False,
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
    revision_range = (
        f"{snapshot.upstream_commit}..{snapshot.head}"
        if snapshot.upstream_commit
        else snapshot.head
    )
    result = _run_repo_git(
        "rev-list",
        "--reverse",
        revision_range,
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
        "--root",
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
                check=False,
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
    if not snapshot.upstream_commit:
        empty_tree = subprocess.run(
            [
                "git",
                "-C",
                str(SCRIPT_DIR),
                "hash-object",
                "-t",
                "tree",
                "--stdin",
            ],
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if empty_tree.returncode != 0:
            _git_failure("Creating an empty comparison tree", empty_tree)
            return None
        result = _run_repo_git(
            "diff",
            "--binary",
            "--no-ext-diff",
            empty_tree.stdout.strip(),
            snapshot.head,
            "--",
        )
    else:
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
    if head.returncode != 0:
        _git_failure("Reading the current data repository commit", head)
        return None
    branch_name = branch.stdout.strip()

    if configured_remote_provider() == "gdrive":
        from .gdrive import GDriveClient

        if branch_name != "main":
            log_error("Google Drive data repository must use the main branch.")
            return None
        client = GDriveClient()
        head_info = client.get_head_info()
        remote_commit = (head_info or {}).get("commit", "")
        return _PushSnapshot(
            branch=branch_name,
            head=head.stdout.strip(),
            upstream_remote="gdrive",
            upstream_ref=branch_name,
            upstream_commit=remote_commit,
        )

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
    revision_range = (
        f"{snapshot.upstream_commit}..{snapshot.head}"
        if snapshot.upstream_commit
        else snapshot.head
    )
    merges = _run_repo_git(
        "rev-list",
        "--min-parents=2",
        revision_range,
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
        if _ALLOW_SECRET_PATHS:
            log_warn("Credential-content check skipped (--allow-secrets):")
            for path in secret_paths:
                print(f"  {path}")
        else:
            log_error("Potential credential content exists in local commits:")
            for path in secret_paths:
                print(f"  {path}")
            log_info(
                "False positive (docs/examples)? Re-run with "
                f"{ENTRYPOINT} push --allow-secrets after reviewing the list"
            )
            return False

    if snapshot.upstream_commit:
        check = _run_repo_git(
            "diff",
            "--check",
            f"{snapshot.upstream_commit}..{snapshot.head}",
            "--",
        )
    else:
        check = _run_repo_git("diff-tree", "--check", "--root", snapshot.head)
    _warn_whitespace_issues(check)
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

    if configured_remote_provider() == "git":
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


def _validate_staged_push(selected: list[str]) -> bool:
    if MEMORY_SCOPE in selected:
        staged = _staged_paths()
        if staged is None or not _memory_root_available(selected, staged):
            return False
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
        if _ALLOW_SECRET_PATHS:
            log_warn("Credential-content check skipped (--allow-secrets):")
            for path in secret_paths:
                print(f"  {path}")
        else:
            log_error(
                "Potential credential content would be committed; push cancelled:"
            )
            for path in secret_paths:
                print(f"  {path}")
            log_info(
                "False positive (docs/examples)? Re-run with "
                f"{ENTRYPOINT} push --allow-secrets after reviewing the list"
            )
            return False

    # 只推記憶時(排程走的就是這條)才把檢查縮到那個目錄:設定目錄裡還沒決定
    # 要不要提交的改動,不該讓沒人看著的排程整晚推不出去。其他範圍維持原樣,
    # 連 repo 根目錄的檔案都要算進去 —— 那是人在選,漏掉一塊值得停下來說。
    scope = [f"{MEMORY_SCOPE}/"] if selected == [MEMORY_SCOPE] else []
    unstaged = _run_repo_git("diff", "--quiet", "--", *scope)
    untracked = _run_repo_git(
        "ls-files", "--others", "--exclude-standard", "--", *scope
    )
    if (
        unstaged.returncode not in (0, 1)
        or untracked.returncode != 0
        or unstaged.returncode == 1
        or untracked.stdout.strip()
    ):
        log_error("Unexpected repository changes remain after staging; push cancelled.")
        return False

    check = _run_repo_git("diff", "--cached", "--check")
    _warn_whitespace_issues(check)
    return True


def _warn_whitespace_issues(check: subprocess.CompletedProcess) -> None:
    # 同步的內容包含第三方 skill 文件,行尾空白/檔尾空行不該擋 push;
    # 保留提示讓使用者知道,但不再視為致命錯誤。
    if check.returncode == 0:
        return
    detail = check.stdout.strip() or check.stderr.strip()
    if detail:
        log_warn("Whitespace issues in the synced content (not blocking):")
        for line in detail.splitlines()[:10]:
            print(f"  {line}")
