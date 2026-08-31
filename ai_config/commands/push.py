"""push command: gather, review, commit and push the data repository."""

import json
import re
import subprocess
from dataclasses import dataclass

from ..config import configured_remote_provider
from ..console import (
    log_error,
    log_header,
    log_info,
    log_success,
    log_warn,
)
from ..paths import ALL_TOOLS, ENTRYPOINT, EXCLUDED_FILES, SCRIPT_DIR
from .apply import _init_tools, _selected_tools
from .sync import (
    _git_failure,
    _remote_is_read_only,
    _repository_operation,
    _run_repo_git,
)

_ALLOW_SECRET_PATHS = False
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

    if configured_remote_provider() == "gdrive":
        from ..gdrive import GDriveClient

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
    head = _run_repo_git("rev-parse", "--verify", "--quiet", "HEAD")
    if head.returncode == 0:
        result = _run_repo_git("restore", "--staged", "--", *tools)
    else:
        # unborn HEAD 沒有 restore 的基準;reset 帶 pathspec 可把索引清回未追蹤
        result = _run_repo_git("reset", "-q", "--", *tools)
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
        from ..gdrive import GDriveClient

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
        if check.returncode != 0:
            _git_failure("Validating local commit changes", check)
            return False
    else:
        check = _run_repo_git("diff-tree", "--check", "--root", snapshot.head)
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
    revision_range = (
        f"{snapshot.upstream_commit}..{snapshot.head}"
        if snapshot.upstream_commit
        else snapshot.head
    )
    commit_list = _run_repo_git(
        "log",
        "--reverse",
        "--format=%h %s",
        revision_range,
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

    if configured_remote_provider() == "gdrive":
        from ..gdrive import gdrive_push_upload
        return gdrive_push_upload(SCRIPT_DIR)

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
    return confirm in ("y", "Y")


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


def _proposed_push_commit_message(paths: list[str]) -> str:
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

    parent = _run_repo_git("rev-parse", "--verify", "HEAD")
    initial_gdrive_commit = (
        parent.returncode != 0 and configured_remote_provider() == "gdrive"
    )
    if parent.returncode != 0 and not initial_gdrive_commit:
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
        if head.returncode == 0 and initial_gdrive_commit:
            rollback = _run_repo_git(
                "update-ref",
                "-d",
                "HEAD",
                head.stdout.strip(),
            )
            if rollback.returncode == 0:
                clear_index = _run_repo_git("read-tree", "--empty")
                if clear_index.returncode == 0:
                    log_warn(
                        "The unreviewed initial commit was rolled back and not pushed"
                    )
                else:
                    _git_failure("Clearing the unreviewed index", clear_index)
            else:
                _git_failure("Rolling back the unreviewed local commit", rollback)
                log_warn(
                    f"Local commit {head.stdout.strip()} was created but not pushed"
                )
        elif head.returncode == 0:
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

    if configured_remote_provider() == "gdrive":
        from ..gdrive import gdrive_push_upload
        return gdrive_push_upload(SCRIPT_DIR)

    push = _run_repo_git("push")
    if push.returncode != 0:
        _git_failure("Pushing configuration", push)
        head = _run_repo_git("rev-parse", "--short", "HEAD")
        if head.returncode == 0:
            log_warn(f"Local commit {head.stdout.strip()} was created but not pushed")
        return 1
    log_success("Local configuration committed and pushed")
    return 0


def do_push(tool: str, allow_secrets: bool = False) -> int:
    # 憑證內容檢查的放行旗標:每次呼叫重設,只有 CLI 明示 --allow-secrets
    # 才會為 True(GUI 走不到,維持硬擋)。
    global _ALLOW_SECRET_PATHS
    _ALLOW_SECRET_PATHS = allow_secrets
    log_header("Push local configuration")
    provider = configured_remote_provider()
    if provider == "git" and _remote_is_read_only():
        log_error(
            "This machine has no push access to the data repository."
        )
        log_info(
            "Configuration set up here is read-only: status, pull, and apply "
            "work, but push needs a credential that can write to the remote."
        )
        return 1
    selected = _selected_tools(tool)
    try:
        preflight = _push_preflight(selected)
        if preflight is None:
            return 1
    except FileNotFoundError:
        log_error("git command not found. Please install git.")
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level guard must not crash
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

    reviewed_diff = _stage_push_changes(selected)
    if reviewed_diff is None:
        return 1
    staged_paths = _staged_paths()
    if staged_paths is None:
        _unstage_tools(selected)
        return 1
    commit_message = _proposed_push_commit_message(staged_paths)

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
