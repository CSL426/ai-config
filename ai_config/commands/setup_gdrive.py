"""Setting up a data repository that syncs through Google Drive."""

from pathlib import Path

from ..config import (
    normalize_gdrive_folder,
    normalize_gdrive_space,
    save_data_repo,
)
from ..console import log_info, log_success
from . import setup_git


def setup_gdrive_repository(
    data_dir: Path,
    gdrive_folder: "str | None" = None,
    gdrive_space: "str | None" = None,
) -> Path:
    from ..gdrive_auth import (
        GDriveAuthError,
        GDriveError,
        get_valid_access_token,
        load_token,
        run_oauth_flow,
        token_has_scope,
    )
    from ..gdrive_client import GDriveClient

    data_dir = data_dir.expanduser().absolute()
    if setup_git._is_reparse_point(data_dir):
        raise setup_git.SetupError(
            f"Data repository root cannot be a symlink or junction: {data_dir}"
        )
    if data_dir.exists() and not data_dir.is_dir():
        raise setup_git.SetupError(f"Data repository path is not a directory: {data_dir}")

    space = normalize_gdrive_space(gdrive_space)
    # setup 一律照路徑重新解析,不沿用舊 id:使用者改路徑就是要換資料夾
    folder_path = normalize_gdrive_folder(gdrive_folder)
    try:
        # 換儲存位置就要換 scope,舊 token 一定不適用,直接重新授權
        try:
            if not token_has_scope(load_token() or {}):
                raise GDriveAuthError("scope mismatch")
            get_valid_access_token()
        except GDriveAuthError:
            log_info("Starting Google OAuth login...")
            run_oauth_flow(space=space)

        client = GDriveClient(
            folder_path=folder_path,
            use_configured_id=False,
            space=space,
        )
        log_info(f"Verifying Google Drive access ({client.location_label()})...")
        folder_url = client.verify_setup_access()
        log_success("Google Drive access verified")
        if folder_url:
            log_info(f"設定會同步到「{client.location_label()}」:{folder_url}")
        else:
            log_info(f"設定會存在{client.location_label()},Drive 介面看不到")
        folder_id = client.get_folder_id() if folder_url else None
    except GDriveError as exc:
        raise setup_git.SetupError(f"Google Drive setup failed: {exc}") from exc

    if data_dir.exists():
        probe = setup_git._run_git(
            "rev-parse",
            "--show-toplevel",
            cwd=data_dir,
            check=False,
        )
        if probe.returncode == 0:
            setup_git._repository_root(data_dir)
        elif any(data_dir.iterdir()):
            raise setup_git.SetupError(f"Data directory is not a Git repository: {data_dir}")
        else:
            setup_git._run_git("init", "-b", "main", cwd=data_dir)
    else:
        data_dir.mkdir(parents=True)
        setup_git._run_git("init", "-b", "main", cwd=data_dir)

    branch = setup_git._run_git(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        cwd=data_dir,
        check=False,
    )
    if branch.returncode != 0 or branch.stdout.strip() != "main":
        raise setup_git.SetupError("Google Drive data repository must use the main branch.")

    for tool in ("claude", "codex", "agy"):
        (data_dir / tool).mkdir(exist_ok=True)

    saved_path = save_data_repo(
        data_dir,
        remote_provider="gdrive",
        gdrive_folder=folder_path,
        gdrive_folder_id=folder_id,
        gdrive_space=space,
    )
    setup_git._ensure_commit_identity(data_dir)
    log_success(f"Data repository configured for Google Drive: {data_dir}")
    log_info(f"Saved configuration: {saved_path}")
    return data_dir
