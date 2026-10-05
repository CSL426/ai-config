"""Which data-repository commit this machine's live configuration matches.

A push gathers the live configuration into the repository. On a machine
that has not applied the latest configuration, that gather writes the
machine's older copy over settings another machine pushed since: on one
machine a push all nearly reverted a day of skill fixes. Each apply and
push records the commit the live configuration now matches; a later push
asks before gathering over commits made after it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from .console import confirm, log_info, log_warn
from .paths import ALL_TOOLS, ENTRYPOINT, HOME, SCRIPT_DIR


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "acg" / "applied.json"


def _git(*args: str) -> "str | None":
    try:
        done = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _load() -> dict:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _repo_key() -> str:
    # 換過資料庫的機器,舊庫的 commit 對新庫沒有意義
    return str(SCRIPT_DIR.resolve())


def record(tools: "list[str]") -> None:
    """Remember that these tools' live configuration matches HEAD now."""
    head = (_git("rev-parse", "HEAD") or "").strip()
    tools = [tool for tool in tools if tool in ALL_TOOLS]
    if not head or not tools:
        return
    state = _load()
    entry = state.get(_repo_key())
    entry = entry if isinstance(entry, dict) else {}
    entry.update(dict.fromkeys(tools, head))
    state[_repo_key()] = entry
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # 記不下來只是少一層保護,不能讓 apply/push 失敗


def has_record() -> bool:
    """Whether this machine ever applied or pushed this data repository."""
    entry = _load().get(_repo_key())
    return isinstance(entry, dict) and any(entry.get(tool) for tool in ALL_TOOLS)


def repo_has_config(data_dir: Path) -> bool:
    """Whether any tool has saved configuration; an empty folder or .gitkeep is not."""
    for tool in ALL_TOOLS:
        root = data_dir / tool
        if root.is_dir() and any(
            item.is_file() and item.name != ".gitkeep" for item in root.rglob("*")
        ):
            return True
    return False


def newer_commits(tool: str) -> "list[str]":
    """Commits touching this tool since this machine last matched the repository."""
    entry = _load().get(_repo_key())
    recorded = entry.get(tool) if isinstance(entry, dict) else None
    if not isinstance(recorded, str) or not recorded:
        return []  # 從沒 apply/push 過:無從判斷,不擋
    listed = _git("log", "--format=%h %ad %s", "--date=short",
                  f"{recorded}..HEAD", "--", f"{tool}/")
    return [line for line in (listed or "").splitlines() if line.strip()]


def confirm_gather(tools: "list[str]", overwrite: bool = False) -> bool:
    """Ask before a push gathers over configuration this machine never applied."""
    newer = {tool: commits for tool in tools if (commits := newer_commits(tool))}
    if not newer:
        return True
    log_warn(
        "這台上次 apply 之後,資料庫又收到其他機器的設定更新,這台還沒套用:"
    )
    for tool, commits in newer.items():
        print(f"  {tool}:")
        for line in commits[:5]:
            print(f"    {line}")
        if len(commits) > 5:
            print(f"    …(另外 {len(commits) - 5} 筆)")
    log_info(
        "現在 push 會先收集這台的設定,把上面這些改動蓋回這台的舊版本。"
    )
    names = " ".join(newer) if len(newer) < len(ALL_TOOLS) else ""
    log_info(f"建議先執行 {ENTRYPOINT} apply {names}".rstrip() + ",再 push")
    if overwrite:
        log_warn("已指定 --overwrite-newer:用這台的版本覆蓋上面這些更新")
        return True
    if not sys.stdin.isatty():
        # 沒有終端機就沒有人能回答。讀 stdin 的話,開著卻沒人寫的管道
        # (agent 的背景 shell)會讓 push 永遠卡住
        log_info("確定要用這台的版本覆蓋,才加 --overwrite-newer 重新執行")
        return False
    # --force 跳不過:這一步要人判斷是不是真的要覆蓋別台的更新
    return confirm("確定要用這台的版本覆蓋嗎? [y/N] ", forceable=False)
