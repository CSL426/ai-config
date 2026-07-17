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

from .config import ConfigError, config_path, default_data_repo, save_data_repo
from .console import log_error, log_info, log_success


class SetupError(RuntimeError):
    """Raised when repository setup cannot be completed safely."""


def _redact_git_output(value: str) -> str:
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", value)


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
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SetupError("Git is required but was not found in PATH.") from exc
    if check and result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or "unknown Git error"
        )
        detail = _redact_git_output(detail)
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


def _remote_refs(data_dir: Path, remote_name: str) -> str:
    output = _run_git("ls-remote", "--refs", remote_name, cwd=data_dir).stdout
    return "\n".join(sorted(output.splitlines()))


def verify_push_access(data_dir: Path, remote_name: str = "origin") -> None:
    local_head = _run_git(
        "rev-parse",
        "--verify",
        "HEAD",
        cwd=data_dir,
    ).stdout.strip()
    refs_before = _remote_refs(data_dir, remote_name)
    check_ref = f"refs/heads/ai-config-write-check-{uuid.uuid4().hex}"
    result = _run_git(
        "push",
        "--porcelain",
        remote_name,
        f"HEAD:{check_ref}",
        cwd=data_dir,
        check=False,
    )
    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or "permission denied"
        )
        detail = _redact_git_output(detail)
        raise SetupError(f"Push permission verification failed: {detail}")

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
            detail = (
                cleanup.stderr.strip()
                or cleanup.stdout.strip()
                or "unknown error"
            )
            detail = _redact_git_output(detail)
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
        log_info(
            f"Verifying push access to remote {remote_name!r} "
            "with a temporary ref"
        )
        verify_push_access(repository, remote_name)
        saved_path = save_data_repo(repository)
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
    log_success("Push access verified; temporary ref was removed")
    log_info(f"Saved configuration: {saved_path}")
    return repository


def _prompt(label: str, default: "str | None" = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


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
    return parser


def run_setup(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    interactive = sys.stdin.isatty()
    data_value = args.data_dir
    if not data_value and interactive:
        data_value = _prompt(
            "Data repository directory",
            str(default_data_repo()),
        )
    if not data_value:
        log_error("--data-dir is required in non-interactive mode.")
        return 2

    data_dir = Path(data_value).expanduser()
    try:
        repo_url = args.repo_url
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
