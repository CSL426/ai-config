"""acg setup: ask for the data repository and hand it to the right provider."""

import argparse
import sys
from pathlib import Path

from ..config import (
    GDRIVE_FOLDER_DEFAULT,
    ConfigError,
    config_path,
    configured_data_repo,
    default_data_repo,
    normalize_gdrive_space,
)
from ..console import ask, log_error, log_info
from . import setup_gdrive, setup_git


class SetupCancelled(setup_git.SetupError):
    """Raised when the user declines an interactive prompt."""


def _prompt(label: str, default: "str | None" = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = ask(f"{label}{suffix}: ")
    if answer is None:
        raise SetupCancelled("Cancelled")
    value = answer.strip()
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
        setup_git._repository_root(data_dir)
    except setup_git.SetupError:
        return False
    return setup_git._remote_url(data_dir, remote_name) is not None


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
        "--account",
        help=(
            "GitHub account (already logged in with gh) to bind to the data "
            "repository; needed for private HTTPS repositories"
        ),
    )
    parser.add_argument(
        "--provider",
        choices=["git", "gdrive"],
        default="git",
        help="Remote sync provider (git or gdrive)",
    )
    parser.add_argument(
        "--gdrive-space",
        choices=["visible", "hidden"],
        help=(
            "Where the Drive files live: visible (default, a folder in My "
            "Drive) or hidden (Google's private app space)"
        ),
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

    try:
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
            gdrive_space = args.gdrive_space
            if gdrive_space is None and interactive:
                print("設定檔要存在 Google Drive 的哪裡?")
                print("  1) 我的雲端硬碟裡的資料夾 (預設,自己看得到、可搬動)")
                print("  2) 隱藏的應用程式空間 (Drive 介面看不到,不弄亂檔案列表)")
                gdrive_space = (
                    "hidden"
                    if _prompt("選擇儲存位置 (1/2)", "1") in ("2", "hidden")
                    else "visible"
                )
            gdrive_space = normalize_gdrive_space(gdrive_space)

            gdrive_folder = args.gdrive_folder
            if gdrive_space == "visible" and gdrive_folder is None and interactive:
                gdrive_folder = _prompt(
                    "Google Drive 資料夾(相對於「我的雲端硬碟」)",
                    GDRIVE_FOLDER_DEFAULT,
                )
            setup_gdrive.setup_gdrive_repository(data_dir, gdrive_folder, gdrive_space)
            return 0

        if (
            not repo_url
            and interactive
            and not _has_usable_remote(
                data_dir,
                args.remote_name,
            )
        ):
            repo_url = _prompt("Data repository Git URL")
        setup_git.setup_repository(
            data_dir,
            repo_url=repo_url or None,
            remote_name=args.remote_name,
            replace_remote=args.replace_remote,
            account=(args.account or "").strip() or None,
        )
    except SetupCancelled:
        log_info("Cancelled; nothing was changed")
        return 130
    except (ConfigError, setup_git.SetupError) as exc:
        log_error(str(exc))
        log_info(f"Configuration was not saved to {config_path()}")
        return 1
    return 0
