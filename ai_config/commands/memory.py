"""acg memory: route each subcommand to the module that carries it out."""

from pathlib import Path

from .. import memory_index, memory_paths
from ..console import HELP_FLAGS, log_error, log_info, log_success, log_warn
from ..paths import ENTRYPOINT
from . import memory_handoff, memory_lifecycle

USAGE = (
    f"Usage: {ENTRYPOINT} memory "
    "<status|enable [codex|agy]|disable [codex|agy]|"
    "adopt [路徑|all|--scan [目錄]]|release [路徑]|path|push|"
    "handoff|autopush>"
)


def run_memory(args: list[str]) -> int:
    try:
        return _run_memory(args)
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1


def _run_memory(args: list[str]) -> int:
    command = args[0] if args else "status"
    rest = args[1:]
    if command in HELP_FLAGS:
        log_info(USAGE)
        return 0
    if command == "status" and not rest:
        return memory_lifecycle._status()
    if command in {"enable", "disable"} and rest in (["codex"], ["agy"]):
        return memory_lifecycle._host(command, rest[0])
    # 別的指令都吃 all,所以這裡也該吃;--scan 留著不動
    if command == "adopt" and rest and rest[0] == "all":
        return memory_lifecycle._adopt_scan(rest[1:])
    if command in {"adopt", "release"} and rest and rest[0] != "--scan":
        if len(rest) > 1:
            log_error(f"Usage: {ENTRYPOINT} memory {command} [專案路徑|all|--scan [目錄]]")
            return 1
        chosen = Path(rest[0]).expanduser()
        if not chosen.is_dir():
            log_error(f"找不到這個專案目錄:{chosen}")
            return 1
        return memory_lifecycle.execute(command, memory_paths.project_root(chosen)).code
    if command == "adopt" and rest and rest[0] == "--scan":
        return memory_lifecycle._adopt_scan(rest[1:])
    if command in {"enable", "disable", "adopt", "release"} and not rest:
        project = memory_paths.project_root() if command in {"adopt", "release"} else None
        return memory_lifecycle.execute(command, project).code
    if command == "path" and set(rest) <= {"--global", "--project"}:
        return _path(rest)
    if command == "autopush":
        return _autopush(rest)
    if command == "push":
        from .. import autopush as auto
        from ..push_preflight import MEMORY_SCOPE
        from .push import do_push

        stale = None
        if len(rest) == 2 and rest[0] == "--if-stale":
            try:
                stale = float(rest[1])
            except ValueError:
                log_error("--if-stale 要接小時數,例如 --if-stale 12")
                return 1
            rest = []
        if stale is not None:
            decision = auto.decide(stale)
            # 時間表住在記憶目錄裡,decide 會先把落後的部分接上,所以讓位要在
            # 它之後才看得到別台剛認領的時段。反過來的話,撞號要等到隔天才發現
            moved = auto.reconcile_slot()
            if moved:
                log_info(moved)
            if not decision.push:
                log_info(f"跳過自動推送:{decision.reason}")
                return 0
        allow_secrets = rest == ["--allow-secrets"]
        if rest and not allow_secrets:
            log_error(
                f"Usage: {ENTRYPOINT} memory push "
                "[--allow-secrets] [--if-stale <小時>]"
            )
            return 1
        if stale is not None:
            # 排程沒有終端機,確認提示讀到 EOF 就會當成拒絕。只跳過確認,
            # 憑證檢查照跑:allow_secrets 維持 False
            from ..console import set_force

            set_force(True)
            return auto.push_and_record(
                lambda: do_push(MEMORY_SCOPE, allow_secrets=False, scheduled=True),
            )
        return do_push(MEMORY_SCOPE, allow_secrets=allow_secrets)
    if command == "handoff":
        return memory_handoff._handoff(rest)
    log_error(USAGE)
    return 1


_AUTOPUSH_USAGE = (
    f"Usage: {ENTRYPOINT} memory autopush [status | enable [時] | disable]"
)


def _autopush(rest: list[str]) -> int:
    from .. import autopush as auto

    action = rest[0] if rest else "status"
    args = rest[1:]
    if action in HELP_FLAGS:
        log_info(_AUTOPUSH_USAGE)
        return 0
    try:
        if action == "status" and not args:
            state = auto.status()
            log_info(f"平台:{state['platform']}")
            if state["installed"]:
                log_success("已排定每天自動推送記憶")
            else:
                log_info(
                    f"尚未排定;沒有排程時,{ENTRYPOINT} 每次執行也會順手檢查"
                )
            log_info(f"上次推送:{_local_time(state['last_push']) or '沒有紀錄'}")
            failure = state["last_failure"]
            if failure:
                # 只記成功時間的話,被擋下的那一晚在這裡看起來一切正常
                # 走 stdout:stderr 不緩衝,被導向時這行會跑到整段輸出的最前面
                log_warn(
                    f"上次自動推送失敗({_local_time(failure['when'])}):{failure['reason']}"
                )
                for path in failure["paths"]:
                    print(f"  {path}")
                log_info(f"處理後執行 {ENTRYPOINT} memory push;成功就會清掉這筆紀錄")
            log_info(f"現在執行的話:{state['reason']}")
            return 0
        if action == "enable" and len(args) <= 1:
            # None 表示「照時間表分配」;給了數字才是手動指定
            hour = int(args[0]) if args else None
            for line in auto.enable(hour):
                log_success(line)
            return 0
        if action == "disable" and not args:
            for line in auto.disable():
                log_info(line)
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(_AUTOPUSH_USAGE)
    return 1


def _local_time(stamp: str) -> str:
    """An ISO timestamp as this machine's wall clock, to the minute."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return stamp


def _path(flags: list[str]) -> int:
    state = memory_index.inspect()
    if flags == ["--global"]:
        print(state.directory)
    elif flags == ["--project"]:
        print(state.project_dir)
    else:
        print(f"global: {state.directory}")
        stability = "stable" if state.project.stable else "local-only"
        print(f"project: {state.project_dir} ({state.project.key}, {stability})")
    return 0
