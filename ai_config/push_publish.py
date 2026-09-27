"""Staging, committing and pushing once the user has approved the diff."""


from . import push_preflight, push_review
from .commands.sync import _git_failure, _run_repo_git
from .config import configured_remote_provider
from .console import confirm as confirm_prompt
from .console import log_error, log_info, log_success, log_warn
from .paths import ENTRYPOINT, SCRIPT_DIR, tilde


def _unstage_tools(tools: list[str]) -> bool:
    staged = push_preflight._staged_paths()
    if staged is None:
        return False
    tools = push_preflight._changed_scopes(tools, staged)
    if not tools:
        return True
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


def _push_existing_commits(selected: list[str], ahead: int) -> int:
    snapshot = push_preflight._push_snapshot()
    if snapshot is None:
        return 1
    commits = push_preflight._ahead_commits(snapshot)
    if commits is None:
        return 1
    if len(commits) != ahead:
        log_error("Local commit count changed after preflight; push cancelled.")
        return 1
    if not push_preflight._validate_ahead_push(selected, commits, snapshot):
        return 1

    committed_diff = push_preflight._ahead_diff(snapshot)
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
    push_review._print_diff_for_review(
        committed_diff,
        ["diff", revision_range] if snapshot.upstream_commit else None,
        f"git -C {tilde(SCRIPT_DIR)} diff {revision_range}",
    )

    if not confirm_prompt("Push these existing local commits? [y/N] "):
        log_info("Cancelled; existing local commits were not pushed")
        return 0
    if not push_preflight._ahead_push_matches(snapshot, selected, commits):
        return 1

    if configured_remote_provider() == "gdrive":
        from .gdrive_sync import gdrive_push_upload
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


# 「暫存後沒有任何實質差異」不是錯誤:換行符正規化這類變動會讓 git status
# 看得到修改,但暫存差異是空的。呼叫端要分得出它和真正的失敗。
NOTHING_TO_PUSH = "\0nothing-to-push"


def _stage_push_changes(selected: list[str]) -> "str | None":
    working = push_preflight._working_paths()
    if working is None or not push_preflight._memory_root_available(selected, working):
        return None
    # Git rejects absent pathspecs, including optional scopes in older repos.
    # Deleted tracked files remain in working, so their scope is still staged.
    changed = push_preflight._changed_scopes(selected, working)
    if changed:
        stage = _run_repo_git("add", "-A", "--", *changed)
        if stage.returncode != 0:
            _git_failure("Staging collected configuration", stage)
            return None

    if not push_preflight._validate_staged_push(selected):
        _unstage_tools(selected)
        return None

    staged_diff = push_preflight._staged_diff()
    if staged_diff is None:
        _unstage_tools(selected)
        return None
    if not staged_diff:
        _unstage_tools(selected)
        log_success("No configuration changes to push")
        return NOTHING_TO_PUSH
    return staged_diff


def _staged_push_matches(selected: list[str], reviewed_diff: str) -> bool:
    if not push_preflight._validate_staged_push(selected):
        return False
    current_diff = push_preflight._staged_diff()
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
    expected_tree = push_preflight._staged_tree()
    current_diff = push_preflight._staged_diff()
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
        from .gdrive_sync import gdrive_push_upload
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


def _explain_push_refusal() -> None:
    """Turn a refused push into the specific reason, and the fix."""
    from .ghauth_access import check_push_access, describe

    remote = _run_repo_git("config", "--get", "remote.origin.url")
    if remote.returncode != 0:
        return
    # 傳資料儲存庫路徑:這個 repo 綁定了誰就問誰,不要退回 gh 的全域帳號
    status = check_push_access(remote.stdout.strip(), SCRIPT_DIR)
    if not status.repository:
        return
    for line in describe(status):
        log_info(line)
    if status.actionable:
        log_info(f"執行 {ENTRYPOINT} login 連結有權限的帳號")
