"""acg login: connect a GitHub account that can write to the data repository."""

from ..console import log_error, log_header, log_info, log_success, log_warn
from ..ghauth import (
    check_push_access,
    describe,
    run_interactive_login,
    setup_git_credentials,
    switch_account,
)
from ..paths import ENTRYPOINT, SCRIPT_DIR


def _remote_url() -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(SCRIPT_DIR), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run_login(account: "str | None" = None) -> int:
    log_header("Connect a GitHub account")
    remote = _remote_url()
    if not remote:
        log_error("資料儲存庫沒有設定 git 遠端")
        log_info(f"先執行 {ENTRYPOINT} setup 設定遠端")
        return 1

    status = check_push_access(remote)
    if not status.repository:
        log_error(status.detail)
        return 1
    for line in describe(status):
        log_info(line)

    if not status.installed:
        return 1

    if status.can_push:
        # 有權限卻推不動,通常是 git 沒接上 gh 的憑證
        ok, detail = setup_git_credentials()
        if ok:
            log_success("git 已接上 GitHub CLI 的憑證")
            return 0
        log_error(f"無法設定 git 憑證:{detail}")
        return 1

    if account:
        if account not in status.accounts:
            log_error(f"gh 沒有 {account} 這個帳號的登入紀錄")
            log_info(f"改用 {ENTRYPOINT} login 直接登入")
            return 1
        ok, detail = switch_account(account)
        if not ok:
            log_error(f"切換帳號失敗:{detail}")
            return 1
        log_success(f"已切換到 {account}")
    else:
        log_info("接下來會開啟 GitHub 登入流程,請依畫面指示完成。")
        ok, detail = run_interactive_login()
        if not ok:
            log_error(f"登入未完成{f':{detail}' if detail else ''}")
            return 1

    ok, detail = setup_git_credentials()
    if not ok:
        log_warn(f"登入成功但無法設定 git 憑證:{detail}")

    after = check_push_access(remote)
    for line in describe(after):
        log_info(line)
    if not after.can_push:
        log_error("這個帳號仍然無法寫入,請改用有權限的帳號")
        return 1
    log_success(f"已可推送到 {after.repository}")
    log_info(f"現在可以執行 {ENTRYPOINT} push")
    return 0
