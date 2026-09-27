"""Pulling and pushing the data repository through Google Drive bundles."""

import tempfile
from pathlib import Path

from . import gdrive_auth, gdrive_client
from .console import log_error, log_info


def gdrive_pull(repo_dir: Path, tool: str) -> int:
    """§1.3 pull implementation for gdrive provider."""
    from .commands.status import show_status
    from .commands.sync import (
        _git_failure,
        _repository_operation,
        _run_repo_git,
        explain_merge_refusal,
        report_dirty_tracked,
    )
    from .console import log_header, log_success

    log_header("Sync repository changes (Google Drive)")

    operation = _repository_operation(repo_dir)
    if operation is not None:
        if operation != "<invalid>":
            log_error(f"Data repository has a {operation} in progress; pull cancelled.")
        return 1

    # 同 git provider:未追蹤的新檔不影響 fast-forward,不該擋住 pull
    status = _run_repo_git(
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
        repo_dir=repo_dir,
    )
    if status.returncode != 0:
        _git_failure("Reading repository status", status)
        return 1
    report_dirty_tracked(status.stdout)

    branch = _run_repo_git(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        repo_dir=repo_dir,
    )
    if branch.returncode != 0:
        log_error("Data repository is in detached HEAD state; pull cancelled.")
        return 1
    if branch.stdout.strip() != "main":
        log_error("Google Drive data repository must use the main branch.")
        return 1

    client = gdrive_client.GDriveClient()
    head_info = client.get_head_info()

    if not head_info or "commit" not in head_info:
        log_error("遠端為空,請先執行 acg push 上傳設定。")
        return 1

    remote_commit = head_info["commit"]
    local_head_proc = _run_repo_git(
        "rev-parse",
        "--verify",
        "HEAD",
        repo_dir=repo_dir,
    )
    local_head = (
        local_head_proc.stdout.strip() if local_head_proc.returncode == 0 else None
    )

    if remote_commit == local_head:
        log_success("Data repository is already up to date")
        print()
        show_status(tool)
        print()
        log_info("Run acg apply to deploy")
        return 0

    bundle_file = client.find_file("repo.bundle")
    if not bundle_file:
        log_error("Google Drive 上找不到 repo.bundle 檔案")
        return 1

    bundle_bytes = client.download_file_bytes(bundle_file["id"])

    with tempfile.NamedTemporaryFile("wb", suffix=".bundle", delete=False) as tmp:
        tmp.write(bundle_bytes)
        tmp_path = Path(tmp.name)

    try:
        verify = _run_repo_git(
            "bundle",
            "verify",
            str(tmp_path),
            repo_dir=repo_dir,
        )
        if verify.returncode != 0:
            _git_failure("Verifying downloaded repository bundle", verify)
            return 1

        fetch = _run_repo_git(
            "fetch",
            str(tmp_path),
            "main",
            repo_dir=repo_dir,
        )
        if fetch.returncode != 0:
            fetch = _run_repo_git(
                "fetch",
                str(tmp_path),
                "HEAD",
                repo_dir=repo_dir,
            )
            if fetch.returncode != 0:
                _git_failure("Fetching updates from Google Drive bundle", fetch)
                return 1

        fetched_head = _run_repo_git("rev-parse", "FETCH_HEAD", repo_dir=repo_dir)
        if fetched_head.returncode != 0 or fetched_head.stdout.strip() != remote_commit:
            log_error(
                "Google Drive repo.bundle does not match head.json; pull cancelled."
            )
            return 1

        merge_ff = _run_repo_git(
            "merge",
            "--ff-only",
            "FETCH_HEAD",
            repo_dir=repo_dir,
        )
        if merge_ff.returncode != 0:
            if not explain_merge_refusal(merge_ff):
                log_error(
                    "Data repository is not safe to fast-forward; pull cancelled. "
                    "本機有未上傳的提交,先 push 或手動處理。"
                )
            return 1

        log_success("Data repository fast-forwarded from Google Drive")
    finally:
        tmp_path.unlink(missing_ok=True)

    print()
    show_status(tool)
    print()
    log_info("Run acg apply to deploy")
    return 0


def gdrive_push_upload(repo_dir: Path) -> int:
    """Upload a full bundle while narrowing Drive's non-atomic CAS window.

    Google Drive has no atomic compare-and-swap across repo.bundle and
    head.json. Re-reading repo.bundle's revision after upload detects a
    competing write before this client publishes head.json, but cannot make
    the two-file update fully atomic.
    """
    try:
        return _gdrive_push_upload(repo_dir)
    except (gdrive_auth.GDriveError, OSError) as exc:
        log_error(f"Google Drive upload failed: {exc}")
        return 1


def _gdrive_push_upload(repo_dir: Path) -> int:
    from .commands.sync import _git_failure, _run_repo_git
    from .console import log_success

    branch = _run_repo_git(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        repo_dir=repo_dir,
    )
    if branch.returncode != 0 or branch.stdout.strip() != "main":
        log_error("Google Drive data repository must use the main branch.")
        return 1

    local_head_proc = _run_repo_git("rev-parse", "HEAD", repo_dir=repo_dir)
    if local_head_proc.returncode != 0:
        _git_failure("Reading local HEAD", local_head_proc)
        return 1
    local_head = local_head_proc.stdout.strip()

    client = gdrive_client.GDriveClient()

    head_info = client.get_head_info()
    if head_info and "commit" in head_info:
        remote_commit = head_info["commit"]
        if remote_commit != local_head:
            ancestor_check = _run_repo_git(
                "merge-base",
                "--is-ancestor",
                remote_commit,
                local_head,
                repo_dir=repo_dir,
            )
            if ancestor_check.returncode != 0:
                log_error(
                    "Data repository has diverged from Google Drive; push cancelled. "
                    "遠端有較新的提交,請先執行 pull。"
                )
                return 1

    with tempfile.NamedTemporaryFile("wb", suffix=".bundle", delete=False) as tmp:
        tmp_bundle_path = Path(tmp.name)

    try:
        bundle_create = _run_repo_git(
            "bundle",
            "create",
            str(tmp_bundle_path),
            "main",
            repo_dir=repo_dir,
        )
        if bundle_create.returncode != 0:
            _git_failure("Creating repository bundle", bundle_create)
            return 1

        bundle_content = tmp_bundle_path.read_bytes()

        existing_bundle = client.find_file("repo.bundle")
        old_revision = (
            existing_bundle.get("headRevisionId") if existing_bundle else None
        )

        res = client.upload_file(
            "repo.bundle",
            bundle_content,
            file_id=existing_bundle.get("id") if existing_bundle else None,
        )
        uploaded_file_id = res.get("id")
        new_revision = res.get("headRevisionId")

        if (
            not isinstance(uploaded_file_id, str)
            or not isinstance(new_revision, str)
            or not new_revision
            or (old_revision is not None and old_revision == new_revision)
        ):
            log_error("Revision mismatch during Google Drive upload")
            return 1

        observed = client.get_file_metadata(uploaded_file_id)
        if observed.get("headRevisionId") != new_revision:
            log_error("Revision mismatch during Google Drive upload")
            return 1

        client.update_head_info(local_head)
        log_success(
            "Local configuration committed and uploaded to Google Drive "
            f"({client.location_label()})"
        )
        return 0
    finally:
        tmp_bundle_path.unlink(missing_ok=True)
