"""First-run data repository setup and remote write verification."""

import argparse
import os
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from ..config import (
    GDRIVE_FOLDER_DEFAULT,
    ConfigError,
    config_path,
    configured_data_repo,
    default_data_repo,
    normalize_gdrive_folder,
    save_data_repo,
)
from ..console import log_error, log_info, log_success, log_warn


class SetupError(RuntimeError):
    """Raised when repository setup cannot be completed safely."""


class PushAccessError(SetupError):
    """Raised when the remote is readable but refuses a write.

    A subclass of SetupError so existing handlers still catch it, but setup
    treats it as a warning: a machine that can only pull still runs status,
    pull, and apply.
    """


def _redact_git_output(value: str) -> str:
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", value)


def _git_error_detail(
    result: subprocess.CompletedProcess[str],
    fallback: str,
) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or fallback
    return _redact_git_output(detail)


def _run_git(
    *args: str,
    cwd: "Path | None" = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if cwd is not None:
        command.extend(("-C", str(cwd)))
    command.extend(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise SetupError("Git is required but was not found in PATH.") from exc
    if check and result.returncode != 0:
        detail = _git_error_detail(result, "unknown Git error")
        raise SetupError(f"Git command failed: {detail}")
    return result


def _reject_embedded_http_credentials(repo_url: str) -> None:
    parsed = urlsplit(repo_url)
    if parsed.scheme in ("http", "https") and parsed.username is not None:
        raise SetupError(
            "Repository URLs containing credentials are not accepted. "
            "Use Git credential storage or SSH instead."
        )


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _repository_root(data_dir: Path) -> Path:
    result = _run_git("rev-parse", "--show-toplevel", cwd=data_dir)
    root = Path(result.stdout.strip()).resolve()
    try:
        same_directory = os.path.samefile(root, data_dir)
    except (OSError, ValueError):
        same_directory = os.path.normcase(os.path.abspath(root)) == os.path.normcase(
            os.path.abspath(data_dir)
        )
    if not same_directory:
        raise SetupError(
            f"Data directory must be the Git repository root: {data_dir}"
        )
    return root


def _remote_url(data_dir: Path, remote_name: str) -> "str | None":
    result = _run_git(
        "remote",
        "get-url",
        remote_name,
        cwd=data_dir,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _ensure_remote(
    data_dir: Path,
    remote_name: str,
    repo_url: "str | None",
    replace_remote: bool,
) -> str:
    current = _remote_url(data_dir, remote_name)
    if repo_url is None:
        if current is None:
            raise SetupError(
                f"Remote {remote_name!r} is missing. "
                "Provide --repo-url to configure it."
            )
        return current

    _reject_embedded_http_credentials(repo_url)
    if current is None:
        _run_git("remote", "add", remote_name, repo_url, cwd=data_dir)
        return repo_url
    if current == repo_url:
        return current
    if not replace_remote:
        raise SetupError(
            f"Remote {remote_name!r} already points somewhere else. "
            "Use --replace-remote to replace it explicitly."
        )
    _run_git("remote", "set-url", remote_name, repo_url, cwd=data_dir)
    return repo_url


def _ensure_upstream(data_dir: Path, remote_name: str) -> None:
    """Bind the current branch to the same-named remote branch.

    A repo that already existed locally (a Google Drive setup, a hand-made
    `git init`) gets its remote added by setup but never learns which remote
    branch to track, and the very next `acg pull` refuses to run. Fetch once
    and set the upstream so setup leaves a pullable repository behind.
    """
    branch = _run_git(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        cwd=data_dir,
        check=False,
    )
    if branch.returncode != 0:
        return
    branch_name = branch.stdout.strip()
    upstream = _run_git(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        cwd=data_dir,
        check=False,
    )
    if upstream.returncode == 0:
        return

    fetched = _run_git("fetch", remote_name, cwd=data_dir, check=False)
    if fetched.returncode != 0:
        log_warn(
            f"Could not fetch from {remote_name!r}; upstream not set: "
            f"{_git_error_detail(fetched, 'unknown error')}"
        )
        return
    remote_branch = f"{remote_name}/{branch_name}"
    exists = _run_git(
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/remotes/{remote_branch}",
        cwd=data_dir,
        check=False,
    )
    if exists.returncode != 0:
        log_info(
            f"Remote has no {branch_name!r} branch yet; "
            "the first acg push will publish it."
        )
        return

    has_commits = _run_git(
        "rev-parse", "--verify", "--quiet", "HEAD", cwd=data_dir, check=False
    )
    if has_commits.returncode != 0:
        # Unborn branch (fresh `git init`): there is no local commit to track
        # from, so adopt the remote branch outright. Refuse if the tree holds
        # anything, since reset --hard would overwrite it.
        dirty = _run_git(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            cwd=data_dir,
            check=False,
        )
        if dirty.stdout.strip():
            log_warn(
                "Local repository has no commits but contains files; "
                f"upstream not set. Commit or remove them, then run: "
                f"git branch --set-upstream-to={remote_branch} {branch_name}"
            )
            return
        _run_git("reset", "--hard", remote_branch, cwd=data_dir)
        _run_git(
            "branch",
            f"--set-upstream-to={remote_branch}",
            branch_name,
            cwd=data_dir,
        )
        log_success(f"Checked out {remote_branch} and set it as upstream")
        return

    _run_git(
        "branch",
        f"--set-upstream-to={remote_branch}",
        branch_name,
        cwd=data_dir,
    )
    log_success(f"Branch {branch_name!r} now tracks {remote_branch}")


def _remote_refs(data_dir: Path, remote_name: str) -> str:
    output = _run_git("ls-remote", "--refs", remote_name, cwd=data_dir).stdout
    return "\n".join(sorted(output.splitlines()))


def verify_read_access(data_dir: Path, remote_name: str = "origin") -> None:
    """Fetching is the one hard requirement: without it nothing can sync."""
    result = _run_git(
        "ls-remote",
        "--heads",
        remote_name,
        cwd=data_dir,
        check=False,
    )
    if result.returncode != 0:
        detail = _git_error_detail(result, "could not reach the remote")
        raise SetupError(f"Read access verification failed: {detail}")


def _push_check_source(data_dir: Path, remote_name: str) -> str:
    """Pick a commit to push to the temporary verification ref.

    HEAD is the natural choice, but an unborn repository has none; the
    fetched remote branch works just as well since only write access is
    being tested, not the content.
    """
    head = _run_git("rev-parse", "--verify", "--quiet", "HEAD", cwd=data_dir, check=False)
    if head.returncode == 0:
        return head.stdout.strip()
    branch = _run_git(
        "symbolic-ref", "--quiet", "--short", "HEAD", cwd=data_dir, check=False
    )
    if branch.returncode == 0:
        remote_ref = f"refs/remotes/{remote_name}/{branch.stdout.strip()}"
        fetched = _run_git(
            "rev-parse", "--verify", "--quiet", remote_ref, cwd=data_dir, check=False
        )
        if fetched.returncode == 0:
            return fetched.stdout.strip()
    raise SetupError(
        "Cannot verify push access: the local repository has no commits and "
        "the remote branch was not fetched."
    )


def verify_push_access(data_dir: Path, remote_name: str = "origin") -> None:
    local_head = _push_check_source(data_dir, remote_name)
    refs_before = _remote_refs(data_dir, remote_name)
    check_ref = f"refs/heads/ai-config-write-check-{uuid.uuid4().hex}"
    result = _run_git(
        "push",
        "--porcelain",
        remote_name,
        f"{local_head}:{check_ref}",
        cwd=data_dir,
        check=False,
    )
    if result.returncode != 0:
        detail = _git_error_detail(result, "permission denied")
        raise PushAccessError(f"Push permission verification failed: {detail}")

    verification_error = None
    try:
        remote_ref = _run_git(
            "ls-remote",
            remote_name,
            check_ref,
            cwd=data_dir,
        ).stdout.split()
        if len(remote_ref) < 2 or remote_ref[0] != local_head:
            verification_error = SetupError(
                "Temporary verification ref was not created correctly: "
                f"{check_ref}"
            )
    finally:
        cleanup = _run_git(
            "push",
            "--porcelain",
            remote_name,
            f":{check_ref}",
            cwd=data_dir,
            check=False,
        )
        if cleanup.returncode != 0:
            detail = _git_error_detail(cleanup, "unknown error")
            raise SetupError(
                "Could not remove temporary verification ref "
                f"{check_ref}: {detail}. "
                f"Remove it manually with: git push {remote_name} :{check_ref}"
            )

    refs_after = _remote_refs(data_dir, remote_name)
    if refs_after != refs_before:
        raise SetupError(
            "Remote refs were not restored after push verification; "
            "configuration was not saved."
        )
    if verification_error is not None:
        raise verification_error


def _clone_or_open(
    data_dir: Path,
    repo_url: "str | None",
    remote_name: str,
) -> Path:
    if data_dir.exists():
        if _is_reparse_point(data_dir):
            raise SetupError(
                "Data repository root cannot be a symlink or junction: "
                f"{data_dir}"
            )
        if not data_dir.is_dir():
            raise SetupError(
                f"Data repository path is not a directory: {data_dir}"
            )
        probe = _run_git(
            "rev-parse",
            "--show-toplevel",
            cwd=data_dir,
            check=False,
        )
        if probe.returncode == 0:
            return _repository_root(data_dir)
        if repo_url is not None and not any(data_dir.iterdir()):
            _reject_embedded_http_credentials(repo_url)
            _run_git("clone", "--origin", remote_name, repo_url, str(data_dir))
            return _repository_root(data_dir)
        raise SetupError(f"Data directory is not a Git repository: {data_dir}")
    if repo_url is None:
        raise SetupError(
            "The data directory does not exist. "
            "Provide --repo-url to clone it."
        )
    _reject_embedded_http_credentials(repo_url)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    _run_git("clone", "--origin", remote_name, repo_url, str(data_dir))
    return _repository_root(data_dir)


def setup_repository(
    data_dir: Path,
    repo_url: "str | None" = None,
    remote_name: str = "origin",
    replace_remote: bool = False,
) -> Path:
    data_dir = data_dir.expanduser().absolute()
    read_only = False
    repository = _clone_or_open(data_dir, repo_url, remote_name)
    previous_remote = _remote_url(repository, remote_name)
    remote_changed = repo_url is not None and previous_remote != repo_url
    try:
        remote_url = _ensure_remote(
            repository,
            remote_name,
            repo_url,
            replace_remote,
        )
        _reject_embedded_http_credentials(remote_url)
        if not (repository / "claude").is_dir():
            raise SetupError(
                "The repository does not contain the required "
                "claude/ directory: "
                f"{repository}"
            )
        log_info(f"Verifying access to remote {remote_name!r}")
        verify_read_access(repository, remote_name)
        log_success("Read access verified")
        _ensure_upstream(repository, remote_name)
        try:
            verify_push_access(repository, remote_name)
        except PushAccessError as exc:
            read_only = True
            log_warn(str(exc))
            log_warn(
                "No push access; configuring this machine as read-only. "
                "status, pull, and apply work; push does not."
            )
        else:
            log_success("Push access verified; temporary ref was removed")
        # An explicit Git setup must also switch a previously configured
        # Google Drive installation back to the Git transport.
        saved_path = save_data_repo(repository, remote_provider="git")
    except Exception:
        if remote_changed:
            if previous_remote is None:
                _run_git(
                    "remote",
                    "remove",
                    remote_name,
                    cwd=repository,
                    check=False,
                )
            else:
                _run_git(
                    "remote",
                    "set-url",
                    remote_name,
                    previous_remote,
                    cwd=repository,
                    check=False,
                )
        raise
    log_success(f"Data repository configured: {repository}")
    if read_only:
        log_warn("This machine is read-only; acg push is not available here")
    log_info(f"Saved configuration: {saved_path}")
    return repository


def setup_gdrive_repository(
    data_dir: Path,
    gdrive_folder: "str | None" = None,
) -> Path:
    from ..gdrive import (
        GDriveAuthError,
        GDriveClient,
        GDriveError,
        get_valid_access_token,
        run_oauth_flow,
    )

    data_dir = data_dir.expanduser().absolute()
    if _is_reparse_point(data_dir):
        raise SetupError(
            f"Data repository root cannot be a symlink or junction: {data_dir}"
        )
    if data_dir.exists() and not data_dir.is_dir():
        raise SetupError(f"Data repository path is not a directory: {data_dir}")

    try:
        try:
            get_valid_access_token()
        except GDriveAuthError:
            log_info("Starting Google OAuth login...")
            run_oauth_flow()

        # setup 一律照路徑重新解析,不沿用舊 id:使用者改路徑就是要換資料夾
        folder_path = normalize_gdrive_folder(gdrive_folder)
        log_info(f"Verifying Google Drive folder access ({folder_path})...")
        client = GDriveClient(folder_path=folder_path, use_configured_id=False)
        folder_url = client.verify_setup_access()
        log_success("Google Drive access verified")
        if folder_url:
            log_info(
                f"設定會同步到「我的雲端硬碟/{folder_path}」:{folder_url}"
            )
        folder_id = client.get_folder_id() if folder_url else None
    except GDriveError as exc:
        raise SetupError(f"Google Drive setup failed: {exc}") from exc

    if data_dir.exists():
        probe = _run_git(
            "rev-parse",
            "--show-toplevel",
            cwd=data_dir,
            check=False,
        )
        if probe.returncode == 0:
            _repository_root(data_dir)
        elif any(data_dir.iterdir()):
            raise SetupError(f"Data directory is not a Git repository: {data_dir}")
        else:
            _run_git("init", "-b", "main", cwd=data_dir)
    else:
        data_dir.mkdir(parents=True)
        _run_git("init", "-b", "main", cwd=data_dir)

    branch = _run_git(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        cwd=data_dir,
        check=False,
    )
    if branch.returncode != 0 or branch.stdout.strip() != "main":
        raise SetupError("Google Drive data repository must use the main branch.")

    for tool in ("claude", "codex", "agy"):
        (data_dir / tool).mkdir(exist_ok=True)

    saved_path = save_data_repo(
        data_dir,
        remote_provider="gdrive",
        gdrive_folder=folder_path,
        gdrive_folder_id=folder_id,
    )
    log_success(f"Data repository configured for Google Drive: {data_dir}")
    log_info(f"Saved configuration: {saved_path}")
    return data_dir


def _prompt(label: str, default: "str | None" = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def _default_setup_data_repo() -> Path:
    try:
        return configured_data_repo() or default_data_repo()
    except ConfigError:
        return default_data_repo()


def _has_usable_remote(data_dir: Path, remote_name: str) -> bool:
    if not data_dir.is_dir():
        return False
    try:
        _repository_root(data_dir)
    except SetupError:
        return False
    return _remote_url(data_dir, remote_name) is not None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-config setup",
        description="Configure and verify the private data repository.",
    )
    parser.add_argument(
        "--data-dir",
        help="Local directory for the data repository",
    )
    parser.add_argument(
        "--repo-url",
        help="Git URL used to clone or configure remote",
    )
    parser.add_argument(
        "--remote-name",
        default="origin",
        help="Git remote name",
    )
    parser.add_argument(
        "--replace-remote",
        action="store_true",
        help="Explicitly replace a different existing remote URL",
    )
    parser.add_argument(
        "--provider",
        choices=["git", "gdrive"],
        default="git",
        help="Remote sync provider (git or gdrive)",
    )
    parser.add_argument(
        "--gdrive-folder",
        help=(
            "Google Drive folder path relative to My Drive "
            f"(default: {GDRIVE_FOLDER_DEFAULT}; nested like Backups/ai-config)"
        ),
    )
    return parser


def run_setup(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    interactive = sys.stdin.isatty()
    provider = args.provider

    data_value = args.data_dir
    if not data_value and interactive:
        data_value = _prompt(
            "Data repository directory",
            str(_default_setup_data_repo()),
        )
    if not data_value:
        log_error("--data-dir is required in non-interactive mode.")
        return 2

    data_dir = Path(data_value).expanduser()
    try:
        repo_url = args.repo_url
        if (
            interactive
            and not repo_url
            and not _has_usable_remote(data_dir, args.remote_name)
            and argv is not None
            and not any(a.startswith("--provider") for a in argv)
        ):
            print("選擇同步傳輸方式:")
            print("  1) Git URL (預設)")
            print("  2) Google Drive")
            choice = _prompt("選擇同步類型 (1/2)", "1")
            if choice in ("2", "gdrive"):
                provider = "gdrive"

        if provider == "gdrive":
            gdrive_folder = args.gdrive_folder
            if gdrive_folder is None and interactive:
                gdrive_folder = _prompt(
                    "Google Drive 資料夾(相對於「我的雲端硬碟」)",
                    GDRIVE_FOLDER_DEFAULT,
                )
            setup_gdrive_repository(data_dir, gdrive_folder)
            return 0

        if not repo_url and interactive and not _has_usable_remote(
            data_dir,
            args.remote_name,
        ):
            repo_url = _prompt("Data repository Git URL")
        setup_repository(
            data_dir,
            repo_url=repo_url or None,
            remote_name=args.remote_name,
            replace_remote=args.replace_remote,
        )
    except (ConfigError, SetupError) as exc:
        log_error(str(exc))
        log_info(f"Configuration was not saved to {config_path()}")
        return 1
    return 0
