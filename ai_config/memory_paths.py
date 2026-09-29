"""Shared memory: one notebook that Claude Code, Codex and agy all read.

The data repository already travels between machines, so the notebook
lives inside it as an ordinary tracked directory. Every tool gets the
same short instruction block pointing at one link, ~/.claude/shared-memory,
so the block is a constant that can itself be synced inside CLAUDE.md.

This module holds the notebook's paths, the rules block every tool reads,
and the safety checks each memory change starts with.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from .links import _path_identity, _reparse_target, _try_create_junction
from .paths import (
    AGY_CONFIG_RULES,
    CLAUDE_HOME,
    CODEX_HOME,
    HOME,
    MEMORY_DIR_NAME,
    MEMORY_LINK,
    NATIVE_WINDOWS,
    SCRIPT_DIR,
    claude_source_dir,
)
from .safety import codex_agents_shared_target, is_reparse_point

WRITE_OBSERVER: ContextVar = ContextVar("memory_write_observer", default=None)
INDEX_NAME = "MEMORY.md"
TOPICS_NAME = "topics"
PROJECTS_NAME = "projects"
AGY_RULES_NAME = "acg-memory.md"
JOURNAL_DIR_NAME = "journal"
# remember plugin 只讀使用者全域設定裡的 data_dir,{slug} 是它能給的唯一專案變數
REMEMBER_USER_CONFIG = HOME / ".remember" / "config.json"
BLOCK_BEGIN = "<!-- acg:memory:begin -->"
BLOCK_END = "<!-- acg:memory:end -->"
# 規則文字是常數:路徑不隨機器改變,所以可以直接放進同步的 CLAUDE.md
RULES_BLOCK = f"""{BLOCK_BEGIN}
## 共用記憶(由 acg 管理,請勿手動修改此區塊)

- 開始工作前先讀 `~/.claude/shared-memory/MEMORY.md`。目錄不存在就略過整節。
- 判斷專案鍵值:執行 `git remote get-url origin`,取網址最後兩段 `owner/repo`
  (去掉 `.git`),寫成 `owner--repo`。沒有遠端就用專案目錄名稱。
- 若 `~/.claude/shared-memory/projects/<鍵值>/MEMORY.md` 存在也讀它,再依兩份
  索引按需讀取各自 `topics/` 裡的主題文件。
- 只有使用者明確要求「記住」時才寫入,且只寫進這個共用目錄,不寫進工具
  自己的自動記憶。
- 講這個程式庫的決定、陷阱或進度就寫專案層;講使用者偏好或跨專案的事實
  就寫全域層;分不清就問使用者。
- 每則記錄寫結論、日期、適用範圍與來源。不寫憑證、聊天紀錄或暫時狀態,
  避免重複與未證實的猜測。
- 專案層目錄不存在時自行建立 MEMORY.md。`acg memory path` 會印出兩層路徑。
- 若 `~/.claude/shared-memory/projects/<鍵值>/journal/recent.md` 或專案目錄的
  `.remember/recent.md` 存在,讀它了解最近進度。那是 Claude 的工作日誌,唯讀。

### 工作線交接(handoff)

日誌記的是「這個專案發生過什麼」,交接記的是「我這條線做到哪」。兩者不同:
每個 session 都往同一份日誌追加,但交接是一條線一份,接手的 session 接走就結案。

- **開工前**先 `acg memory handoff list`。有待接的線就把名稱與摘要
  告訴使用者,問他要不要接;**不要自己挑**,也不要看到就當作已經接手。
- 使用者說要接,才 `acg memory handoff claim "<名稱>"`。它會印出
  那條線的完整進度,並把這則交接結案、記下是誰接走的。讀完用自己的話
  跟使用者確認理解的接手點,再開始做事。
- 被拒表示這條線已經被別的 session 接走;把是誰告訴使用者,不要重試。
- 列表上標 `⚠ 可能已過期` 的線超過一天沒人接。裡面的進度可能已被別處的工作
  蓋過去 —— 先讀內容再決定,不要照著它的待辦直接做。
- **收工前**若這條線沒做完,`acg memory handoff write "<名稱>" "<內容>"`
  寫一份新的交接;做完了就不用寫。
- 交接內容寫給不知道前因後果的人看。用這幾個標題分段,接手的人才不必讀完
  整篇才知道還剩什麼(`list` 會自動把 `## Next` 的第一項顯示出來):

      ## Goal       這條線要達成什麼
      ## State      現在到哪裡:分支、版本、部署狀態
      ## Verified   **實際驗證過**的結論,附上怎麼驗的
      ## Refuted    試過但不成立的,寫下來才不會有人再試一次
      ## Unknowns   還不確定的,別寫成結論
      ## Next       還沒做的,一項一行

  **Verified 跟 Unknowns 一定要分開。** 把推測寫得像事實,下一個人會照著做決定;
  這個錯誤在這裡真的發生過。沒東西可寫的段落就省略,不要留空標題。
  「繼續」不算交接。
- 這跟 session 開頭 remember 印的 `=== HANDOFF ===`(那只是告訴你日誌檔寫在哪)
  是兩回事,不要混用。
{BLOCK_END}
"""
# 專案層用另一組標記:全域區塊的保留/收集邏輯不能把它當成自己的
PROJECT_BLOCK_BEGIN = "<!-- acg:project-memory:begin -->"
PROJECT_BLOCK_END = "<!-- acg:project-memory:end -->"


def project_rules_block(root: Path) -> str:
    """The same rules, pointing at this machine's notebook instead of the global link.

    On someone else's machine there is no ~/.claude/shared-memory and
    there should not be; the block names the real directory instead.
    """
    return (
        RULES_BLOCK.replace(BLOCK_BEGIN, PROJECT_BLOCK_BEGIN)
        .replace(BLOCK_END, PROJECT_BLOCK_END)
        .replace("~/.claude/shared-memory", root.as_posix())
    )


_BLOCK_RE = re.compile(
    re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.DOTALL
)
# 遠端網址最後兩段:git@host:owner/repo.git、https://host/owner/repo、/srv/owner/repo
_REMOTE_TAIL = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def memory_dir() -> Path:
    return SCRIPT_DIR / MEMORY_DIR_NAME


def index_path() -> Path:
    return memory_dir() / INDEX_NAME


def live_rules_path() -> Path:
    return CLAUDE_HOME / "CLAUDE.md"


def source_rules_path() -> Path:
    return claude_source_dir() / "CLAUDE.md"


def agy_rules_path() -> Path:
    return AGY_CONFIG_RULES / AGY_RULES_NAME


def codex_override_path() -> Path:
    return CODEX_HOME / "AGENTS.override.md"


def codex_rules_path() -> Path:
    return CODEX_HOME / "AGENTS.md"


def instruction_paths() -> list[Path]:
    paths = [live_rules_path(), codex_rules_path()]
    for path in (source_rules_path(), SCRIPT_DIR / "codex" / "AGENTS.md"):
        if os.path.lexists(path):
            paths.append(path)
    return paths


def assert_plain_path(path: Path, *, directory: bool | None = None) -> None:
    """Validate missing destinations too, without following a reparse point."""
    path = Path(os.path.abspath(path))
    for parent in reversed(path.parents):
        if is_reparse_point(parent):
            raise RuntimeError(f"Refusing reparse point parent: {parent}")
        if parent.exists() and not parent.is_dir():
            raise RuntimeError(f"Expected directory: {parent}")
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point: {path}")
    if not path.exists():
        return
    mode = path.stat().st_mode
    valid = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
    if directory is None:
        valid = stat.S_ISDIR(mode) or stat.S_ISREG(mode)
    if not valid:
        raise RuntimeError(f"Unexpected path type: {path}")


def rules_target(path: Path) -> Path:
    if path == codex_rules_path() and is_reparse_point(path):
        assert_plain_path(path.parent, directory=True)
        target = codex_agents_shared_target(path)
        assert target is not None
    else:
        target = path
    assert_plain_path(target, directory=False)
    return target


def _assert_tree(path: Path) -> None:
    assert_plain_path(path)
    if path.is_dir():
        for child in path.iterdir():
            _assert_tree(child)


def preflight_memory() -> None:
    assert_plain_path(memory_dir(), directory=True)
    for path in (index_path(), memory_dir() / ".gitignore"):
        assert_plain_path(path, directory=False)
    for path in (memory_dir() / TOPICS_NAME, memory_dir() / PROJECTS_NAME):
        assert_plain_path(path, directory=True)
        _assert_tree(path)
    assert_plain_path(journal_root(), directory=True)
    if journal_root().is_dir():
        for path in journal_root().iterdir():
            if is_reparse_point(path):
                target = _reparse_target(path)
                if target.name.casefold() != JOURNAL_DIR_NAME or not _path_identity(
                    target.parent.parent, memory_dir() / PROJECTS_NAME
                ):
                    raise RuntimeError(f"Unexpected journal link: {path}")
                assert_plain_path(target, directory=True)
                if not target.is_dir():
                    raise RuntimeError(f"Broken journal link: {path}")
            else:
                _assert_tree(path)


def preflight_rules(*, enabling: bool) -> None:
    for path in instruction_paths():
        target = rules_target(path)
        _validate_block(_read_text(target))
    path = agy_rules_path()
    assert_plain_path(path, directory=False)
    text = _read_text(path)
    _validate_block(text)
    if enabling and path.exists() and not has_block(text):
        raise RuntimeError(f"Refusing unmanaged agy rules file: {path}")
    assert_plain_path(REMEMBER_USER_CONFIG, directory=False)
    _read_user_config()
    assert_plain_path(MEMORY_LINK.parent, directory=True)
    state, detail = link_state()
    if enabling and state not in ("missing", "ok"):
        raise RuntimeError(f"無法建立連結 {MEMORY_LINK}:{detail}")


@dataclass
class ProjectKey:
    key: str
    # 由遠端網址推導的鍵值在每台機器上都相同;目錄名稱則不保證
    stable: bool
    source: str


def sanitize_key(name: str) -> str:
    return _UNSAFE.sub("-", name.strip()).strip("-.") or "unnamed"


def parse_project_key(remote_url: str) -> str:
    match = _REMOTE_TAIL.search(remote_url.strip())
    if not match:
        return ""
    return f"{sanitize_key(match[1])}--{sanitize_key(match[2])}"


def _git_output(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def project_key(cwd: "Path | None" = None) -> ProjectKey:
    here = Path.cwd() if cwd is None else cwd
    remote = _git_output(here, "remote", "get-url", "origin")
    if remote:
        key = parse_project_key(remote)
        if key:
            return ProjectKey(key, True, "remote")
    toplevel = _git_output(here, "rev-parse", "--show-toplevel")
    if toplevel:
        return ProjectKey(sanitize_key(Path(toplevel).name), False, "toplevel")
    return ProjectKey(sanitize_key(here.resolve().name), False, "cwd")


def project_memory_dir(key: ProjectKey) -> Path:
    return memory_dir() / PROJECTS_NAME / key.key


def project_root(cwd: "Path | None" = None) -> Path:
    """The directory Claude Code keys its per-project state to.

    For a linked worktree that is the main checkout, matching what the
    remember plugin does, so every worktree shares one journal.
    """
    here = Path.cwd() if cwd is None else cwd
    common = _git_output(
        here, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if common and Path(common).name == ".git":
        return Path(common).parent
    toplevel = _git_output(here, "rev-parse", "--show-toplevel")
    if toplevel:
        return Path(toplevel)
    return here.resolve()


def session_slug(path: Path) -> str:
    """Claude Code's name for a project directory under ~/.claude/projects/."""
    out = []
    for char in str(path).rstrip("/\\") or str(path):
        if char.isascii() and char.isalnum():
            out.append(char)
        elif ord(char) >= 0x10000:
            out.append("--")
        else:
            out.append("-")
    return "".join(out)


def has_block(text: str) -> bool:
    return _BLOCK_RE.search(text) is not None


def _validate_block(text: str) -> None:
    if BLOCK_BEGIN not in text and BLOCK_END not in text:
        return
    if (
        text.count(BLOCK_BEGIN) != 1
        or text.count(BLOCK_END) != 1
        or not has_block(text)
    ):
        raise RuntimeError("Malformed acg memory instruction block")


def without_block(text: str) -> str:
    stripped = _BLOCK_RE.sub("", text)
    return stripped.rstrip("\n") + "\n" if stripped.strip() else ""


def with_block(text: str) -> str:
    body = without_block(text)
    if body:
        return body + "\n" + RULES_BLOCK
    return RULES_BLOCK


def _umask() -> int:
    """This process's umask. Reading it means setting it, so it is put back.

    Safe here because the CLI is single-threaded; a thread writing a
    file in the microsecond between these two calls would use 0o022.
    """
    value = os.umask(0o022)
    os.umask(value)
    return value


def _write_text_atomic(path: Path, content: str) -> None:
    assert_plain_path(path, directory=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_bytes() if path.exists() else None
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        assert_plain_path(path, directory=False)
        current = path.read_bytes() if path.exists() else None
        if current != previous:
            raise RuntimeError(f"File changed during update: {path}")
        if path.exists():
            shutil.copymode(path, temporary)
        else:
            # mkstemp 固定給 0600,而那是這個檔案往後的權限 —— 只有既有
            # 檔案才會走上面那行。同步目錄裡半數檔案因此比旁邊的更嚴,
            # 換一台機器或換個使用者就讀不到
            os.chmod(temporary, 0o666 & ~_umask())
        os.replace(temporary, path)
        observer = WRITE_OBSERVER.get()
        if observer is not None:
            observer(path, content.encode("utf-8"))
    finally:
        temporary.unlink(missing_ok=True)


def _unlink_file(path: Path) -> None:
    assert_plain_path(path, directory=False)
    path.unlink()
    observer = WRITE_OBSERVER.get()
    if observer is not None:
        observer(path, None)


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    # Path.read_text 在 3.13 之前沒有 newline 參數;保留原始換行才不會改動整個檔案
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def install_block(path: Path) -> bool:
    """Append the block once; return whether the file changed."""
    path = rules_target(path)
    current = _read_text(path)
    _validate_block(current)
    updated = with_block(current)
    if updated == current:
        return False
    _write_text_atomic(path, updated)
    return True


def remove_block(path: Path) -> bool:
    path = rules_target(path)
    if not path.is_file():
        return False
    current = _read_text(path)
    _validate_block(current)
    if not has_block(current):
        return False
    _write_text_atomic(path, without_block(current))
    return True


def install_agy_rules() -> bool:
    path = agy_rules_path()
    assert_plain_path(path, directory=False)
    if path.exists() and not has_block(_read_text(path)):
        raise RuntimeError(f"Refusing unmanaged agy rules file: {path}")
    return install_block(path)


def remove_agy_rules() -> bool:
    path = agy_rules_path()
    assert_plain_path(path, directory=False)
    if not path.is_file() or not has_block(_read_text(path)):
        return False
    remove_block(path)
    if not _read_text(path).strip():
        _unlink_file(path)
    return True


def link_state() -> tuple[str, str]:
    """One of ok / missing / foreign / conflict, plus a detail for the last two."""
    link = MEMORY_LINK
    if not (link.exists() or link.is_symlink() or is_reparse_point(link)):
        return "missing", ""
    if link.is_symlink() or is_reparse_point(link):
        try:
            current = _reparse_target(link)
        except (OSError, RuntimeError) as exc:
            return "foreign", str(exc)
        if _path_identity(current, memory_dir()):
            return "ok", ""
        return "foreign", str(current)
    return "conflict", "已存在同名的一般目錄或檔案"


def create_link() -> tuple[bool, str]:
    assert_plain_path(memory_dir(), directory=True)
    assert_plain_path(MEMORY_LINK.parent, directory=True)
    state, detail = link_state()
    if state == "ok":
        return True, ""
    if state != "missing":
        return False, detail or state
    target = memory_dir()
    target.mkdir(parents=True, exist_ok=True)
    CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
    if NATIVE_WINDOWS:
        if _try_create_junction(target, MEMORY_LINK):
            return True, ""
        return False, "無法建立 Junction"
    try:
        MEMORY_LINK.symlink_to(target)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def remove_link() -> bool:
    state, _ = link_state()
    if state != "ok":
        return False
    if MEMORY_LINK.is_symlink():
        MEMORY_LINK.unlink()
    else:
        os.rmdir(MEMORY_LINK)
    return True


def _read_user_config() -> "dict | None":
    if not REMEMBER_USER_CONFIG.is_file():
        return None
    try:
        data = json.loads(_read_text(REMEMBER_USER_CONFIG))
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid remember configuration: {REMEMBER_USER_CONFIG}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(  # noqa: TRY004 - report malformed user input at CLI boundary
            f"Expected remember config object: {REMEMBER_USER_CONFIG}"
        )
    return data


def journal_root() -> Path:
    return memory_dir() / JOURNAL_DIR_NAME
