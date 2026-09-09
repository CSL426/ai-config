"""acg login: bind a GitHub account that can write to the data repository.

The binding is scoped to the data repository. gh's active account and
every other repository on the machine are left exactly as they were.
"""

from ..console import log_error, log_header, log_info, log_success, log_warn
from ..ghauth import (
    GhAuthError,
    active_account,
    bind_account,
    check_push_access,
    describe,
    run_interactive_login,
    switch_account,
    unbind_account,
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
    if account == "--unbind":
        try:
            unbound = unbind_account(SCRIPT_DIR)
        except GhAuthError as exc:
            log_error(str(exc))
            return 1
        if unbound:
            log_success("已解除資料庫的帳號綁定,改回 git 的一般憑證")
        else:
            log_info("資料庫沒有綁定任何帳號")
        return 0

    remote = _remote_url()
    if not remote:
        log_error("資料儲存庫沒有設定 git 遠端")
        log_info(f"先執行 {ENTRYPOINT} setup 設定遠端")
        return 1

    status = check_push_access(remote, SCRIPT_DIR)
    if not status.repository:
        log_error(status.detail)
        return 1
    for line in describe(status):
        log_info(line)
    if status.can_push and not account:
        log_success(f"已可推送到 {status.repository}")
        return 0
    if not status.installed:
        return 1

    if account:
        if account not in status.accounts:
            log_error(f"gh 沒有 {account} 這個帳號的登入紀錄")
            log_info(f"改用 {ENTRYPOINT} login 直接登入")
            return 1
    elif status.account_can_push and status.account in status.accounts:
        account = status.account
    else:
        # 互動登入會把新帳號設成 gh 的作用中帳號;登入完把它改回去,
        # 新帳號只綁在資料庫上
        previous = active_account()
        log_info("接下來會開啟 GitHub 登入流程,請依畫面指示完成。")
        ok, detail = run_interactive_login()
        account = active_account()
        if previous and previous != account:
            restored, restore_detail = switch_account(previous)
            if not restored:
                log_error(f"無法還原 gh 原本的作用中帳號 {previous}:{restore_detail}")
                return 1
        if not ok:
            log_error(f"登入未完成{f':{detail}' if detail else ''}")
            return 1

    ok, detail = bind_account(SCRIPT_DIR, account)
    if not ok:
        log_error(f"綁定帳號失敗:{detail}")
        return 1
    log_success(f"資料庫已綁定 {account}(只影響這個資料庫)")

    after = check_push_access(remote, SCRIPT_DIR)
    for line in describe(after):
        log_info(line)
    if not after.can_push:
        log_warn(f"解除綁定:{ENTRYPOINT} login --unbind")
        log_error("尚未確認 git 可推送,請檢查遠端協定與憑證設定")
        return 1
    log_success(f"已可推送到 {after.repository}")
    log_info(f"現在可以執行 {ENTRYPOINT} push")
    return 0
