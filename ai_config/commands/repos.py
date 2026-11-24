"""`acg repos`: git repositories this machine keeps in step every night."""

from pathlib import Path

from .. import repos
from ..console import log_error, log_header, log_info, log_success, log_warn
from ..paths import ENTRYPOINT

USAGE = "Usage: {entry} repos [list|add <路徑>|remove <路徑>|sync [路徑]]"


def _local(stamp: str) -> str:
    from datetime import datetime

    try:
        return f"{datetime.fromisoformat(stamp).astimezone():%Y-%m-%d %H:%M}"
    except ValueError:
        return stamp


def _line(repo: Path, result: dict) -> str:
    mark = "✓" if result.get("ok") is True else "✗"
    return f"{mark} {repo}:{result.get('note', '')}"


def _list() -> int:
    from .. import nightly

    log_header("Repos")
    listed = repos.registered()
    if not listed:
        log_info("這台沒有要每晚同步的 repo")
        log_info(f"加入:{ENTRYPOINT} repos add <路徑>(每台各自選,不會同步到別台)")
        return 0
    if nightly.enabled("repos"):
        log_success("每晚排程會同步以下 repo(跟記憶上傳共用同一個時段)")
    else:
        log_warn(f"每晚排程沒開;{ENTRYPOINT} repos add 任一個已登記的 repo 會重新打開")
    last = {str(repo): result for repo, result in repos.results()}
    for repo in listed:
        result = last.get(str(repo))
        if result is None:
            print(f"  · {repo}:還沒同步過")
        else:
            print(f"  {_line(repo, result)}({_local(str(result.get('when', '')))})")
    if repos.failures():
        log_warn(f"✗ 的 repo 處理後可用 {ENTRYPOINT} repos sync <路徑> 立即重跑")
    return 0


def _sync(only: "Path | None") -> int:
    code, done = repos.sync(only)
    if not done:
        log_info(f"清單是空的;{ENTRYPOINT} repos add <路徑> 加入")
    for repo, result in done:
        print(_line(repo, result), flush=True)
    return code


def run_repos(args: list) -> int:
    action = args[0] if args else "list"
    if action in {"--help", "-h"}:
        log_info(USAGE.format(entry=ENTRYPOINT))
        return 0
    try:
        if action in {"list", "status"} and len(args) <= 1:
            return _list()
        if action == "add" and len(args) == 2:
            for line in repos.add(Path(args[1])):
                log_info(line)
            return 0
        if action == "remove" and len(args) == 2:
            for line in repos.remove(Path(args[1])):
                log_info(line)
            return 0
        if action == "sync" and len(args) <= 2:
            return _sync(Path(args[1]) if len(args) == 2 else None)
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(USAGE.format(entry=ENTRYPOINT))
    return 1
