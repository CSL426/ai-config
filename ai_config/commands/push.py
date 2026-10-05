"""push command: gather, review, commit and push the data repository.

The steps live in push_preflight, push_review and push_publish; this is
the order they run in and the CLI's exit codes.
"""

from .. import applied_state, push_preflight, push_publish, push_review
from ..config import configured_remote_provider
from ..console import log_error, log_header, log_info, log_success
from .apply import _init_tools, _selected_tools
from .sync import _git_failure, _remote_is_read_only, _run_repo_git


def do_push(
    tool: str, allow_secrets: bool = False, scheduled: bool = False,
    overwrite_newer: bool = False,
) -> int:
    # 憑證內容檢查的放行旗標:每次呼叫重設,只有 CLI 明示 --allow-secrets
    # 才會為 True(GUI 走不到,維持硬擋)。
    push_preflight._ALLOW_SECRET_PATHS = allow_secrets
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
        push_publish._explain_push_refusal()
        return 1
    selected = push_preflight._push_scopes(tool)
    try:
        preflight = push_preflight._push_preflight(selected)
        if preflight is None:
            return 1
    except FileNotFoundError:
        log_error("git command not found. Please install git.")
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level guard must not crash
        log_error(f"Failed to prepare repository push: {exc}")
        return 1

    if preflight.ahead:
        return _recorded(selected, push_publish._push_existing_commits(selected, preflight.ahead))

    # 記憶隨時都可能有未保存的修改;只有它髒不能讓工具設定跳過收集
    if preflight.has_changes and not push_preflight._only_memory_changes():
        log_info("Reviewing existing uncommitted configuration changes")
    elif tool != push_preflight.MEMORY_SCOPE:
        if not applied_state.confirm_gather(_selected_tools(tool), overwrite_newer):
            log_info("Cancelled; nothing was gathered")
            return 1
        if not _init_tools(tool):
            return 1

    status = _run_repo_git("status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        _git_failure("Reading collected configuration changes", status)
        return 1
    pending = status.stdout
    if not pending.strip():
        log_success("No local configuration changes to push")
        return 0

    reviewed_diff = push_publish._stage_push_changes(selected)
    if reviewed_diff is push_publish.NOTHING_TO_PUSH:
        return 0
    if reviewed_diff is None:
        return 1
    staged_paths = push_preflight._staged_paths()
    if staged_paths is None:
        push_publish._unstage_tools(selected)
        return 1
    commit_message = push_review._proposed_push_commit_message(staged_paths, scheduled)

    confirmed = False
    ready_to_commit = False
    cleanup_succeeded = True
    try:
        confirmed = push_review._review_and_confirm_push(
            pending,
            reviewed_diff,
            commit_message,
        )
        if confirmed:
            ready_to_commit = push_publish._staged_push_matches(selected, reviewed_diff)
    finally:
        if not ready_to_commit:
            cleanup_succeeded = push_publish._unstage_tools(selected)

    if not confirmed:
        if cleanup_succeeded:
            # 非 0:呼叫端要分得出「推上去了」和「沒推」,--force 可跳過確認
            log_info("Cancelled; configuration changes remain unstaged")
            return 1
        log_error("Cancellation failed to restore the staged configuration.")
        return 1
    if not ready_to_commit:
        return 1
    return _recorded(
        selected, push_publish._commit_and_push(commit_message, selected, reviewed_diff)
    )


def _recorded(selected: "list[str]", result: int) -> int:
    # 推上去之後,這台的設定就是資料庫的 HEAD
    if result == 0:
        applied_state.record(selected)
    return result
