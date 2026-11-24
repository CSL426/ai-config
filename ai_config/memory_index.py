"""The notebook's index, its drift and secret scans, and memory status."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import memory_journal, memory_paths
from .links import _path_identity
from .safety import SECRET_PATTERN, is_reparse_point

INDEX_TEMPLATE = """# 共用記憶

全域索引。每則一行:`- [標題](topics/檔名.md) — 一句話摘要`。
專案記憶放在 `projects/<owner--repo>/MEMORY.md`,格式相同。
"""


def ensure_index() -> bool:
    """Create the notebook skeleton; return whether anything was written."""
    root = memory_paths.memory_dir()
    memory_paths.preflight_memory()
    path = memory_paths.index_path()
    if path.is_file():
        return False
    root.mkdir(parents=True, exist_ok=True)
    (root / memory_paths.TOPICS_NAME).mkdir(exist_ok=True)
    memory_paths._write_text_atomic(path, INDEX_TEMPLATE)
    return True


_INDEX_LINK = re.compile(r"^\s*-\s*\[[^\]]*\]\(([^)]+)\)", re.MULTILINE)


def index_drift(index: Path, subdir: str) -> tuple[list[str], list[str]]:
    """Notes the index forgot, and index lines pointing at nothing.

    The index is written by hand: its one-line summaries cannot be
    regenerated from the notes themselves. So this reports the two
    drifts instead of repairing them — a note nobody linked is invisible
    to the next session, and a dead link wastes the reader's time.
    """
    root = index.parent / subdir
    if not index.is_file():
        return [], []
    linked = set()
    for target in _INDEX_LINK.findall(memory_paths._read_text(index)):
        # 外部網址要在切成檔名之前排除,否則 scheme 已經被切掉認不出來
        if "://" in target or target.startswith("mailto:"):
            continue
        name = target.replace("\\", "/").split("/")[-1].split("#")[0]
        if name:
            linked.add(name)
    present = set()
    if root.is_dir():
        present = {
            item.name
            for item in root.iterdir()
            if item.is_file() and item.suffix == ".md"
        }
    return sorted(present - linked), sorted(linked - present)


def secret_notes() -> list[str]:
    """Notes that sync and look like they carry a credential.

    acg never writes the notes itself — an AI session does, straight to
    disk. push already refuses to commit a secret, but that is the last
    gate; saying so at status time means the note can be fixed before
    it is ever staged.
    """
    root = memory_paths.memory_dir()
    if not root.is_dir():
        return []
    synced = _synced_files(root)
    if synced is None:
        # git 答不出來時寧可多報:少報一則,真的憑證就可能沒人看到
        synced = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.relative_to(root).parts[0]
            not in (memory_paths.JOURNAL_DIR_NAME, ".git")
        ]
    return sorted(path for path in synced if carries_secret(root / path))


def _synced_files(root: Path) -> "list[str] | None":
    """Files under the notebook that git would carry, relative to it.

    Git decides, not a list of folders: memory/.gitignore keeps only the
    root journal on this machine, while project journals and handoffs sync.
    A hand-kept list once skipped every journal and missed the very note
    that kept a Windows machine's notebook local. Outside any repository
    nothing syncs; None means git could not say.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=20,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return [] if "not a git repository" in result.stderr else None
    return [path for path in result.stdout.split("\0") if path]


def carries_secret(path: Path) -> bool:
    """The check push makes on staged content, made on a file on disk."""
    if is_reparse_point(path) or not path.is_file():
        return False
    try:
        return bool(SECRET_PATTERN.search(path.read_bytes()))
    except OSError:
        return False


def git_changes() -> "list[str] | None":
    """Uncommitted paths under memory/, or None when the repo is not git."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(memory_paths.SCRIPT_DIR),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                memory_paths.MEMORY_DIR_NAME,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line[3:] for line in result.stdout.splitlines() if line.strip()]


@dataclass
class MemoryStatus:
    directory: Path
    index_exists: bool
    link: str
    link_detail: str
    live_block: bool
    source_block: bool
    source_exists: bool
    agy_rules: bool
    codex_block: bool
    codex_override: bool
    changes: "list[str] | None"
    project: memory_paths.ProjectKey
    project_dir: Path
    project_exists: bool
    remember: bool
    journal_config: str
    journal_config_value: str
    journal: str
    journal_detail: str
    index_unlisted: list[str]
    index_dangling: list[str]
    secret_notes: list[str]

    @property
    def enabled(self) -> bool:
        return self.link == "ok" and self.live_block


def inspect(cwd: "Path | None" = None) -> MemoryStatus:
    state, detail = memory_paths.link_state()
    key = memory_paths.project_key(cwd)
    project_dir = memory_paths.project_memory_dir(key)
    root = memory_paths.project_root(cwd)
    config_state, config_value = memory_journal.journal_config_state()
    journal, journal_detail = memory_journal.journal_state(root)
    unlisted, dangling = index_drift(memory_paths.index_path(), memory_paths.TOPICS_NAME)
    return MemoryStatus(
        directory=memory_paths.memory_dir(),
        index_exists=memory_paths.index_path().is_file(),
        link=state,
        link_detail=detail,
        live_block=memory_paths.has_block(memory_paths._read_text(memory_paths.live_rules_path())),
        source_block=memory_paths.has_block(memory_paths._read_text(memory_paths.source_rules_path())),
        source_exists=memory_paths.source_rules_path().is_file(),
        agy_rules=memory_paths.has_block(memory_paths._read_text(memory_paths.agy_rules_path())),
        codex_block=memory_paths.has_block(memory_paths._read_text(memory_paths.codex_rules_path())),
        codex_override=memory_paths.codex_override_path().is_file(),
        changes=git_changes(),
        project=key,
        project_dir=project_dir,
        project_exists=(project_dir / memory_paths.INDEX_NAME).is_file(),
        remember=memory_journal.remember_installed(),
        journal_config=config_state,
        journal_config_value=config_value,
        journal=journal,
        journal_detail=journal_detail,
        index_unlisted=unlisted,
        index_dangling=dangling,
        secret_notes=secret_notes(),
    )


def entry_status(path: Path) -> dict:
    """Inspect a rule entry without following unknown links or malformed blocks."""
    target = path
    try:
        target = memory_paths.rules_target(path)
        text = memory_paths._read_text(target)
        memory_paths._validate_block(text)
        installed = memory_paths.has_block(text)
        reason = "規則已安裝，請開新會話驗證" if installed else "尚未安裝 acg 記憶規則"
        status = "installed" if installed else "missing"
        if path == memory_paths.codex_rules_path() and memory_paths.codex_override_path().is_file():
            status = "blocked"
            reason = "AGENTS.override.md 會遮蔽共用規則"
        if path == memory_paths.agy_rules_path() and target.exists() and not installed:
            status, reason = "blocked", "既有 agy 規則未由 acg 管理"
    except (OSError, RuntimeError, ValueError) as exc:
        status, reason = "blocked", str(exc)
    return {
        "path": str(path),
        "target": str(target),
        "status": status,
        "reason": reason,
    }


_SCAN_SKIP = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".cache",
    ".hf_cache", "dist", "build", ".worktree", ".worktrees", "site-packages",
}


def journals_below(root: Path, depth: int = 4) -> list:
    """Every project under root that keeps a journal.

    The name of a project's directory cannot be recovered from Claude
    Code's own registry — a hyphen there may be a separator or part of the
    name, and `ai-config` decodes to `ai/config`. Walking for the journal
    itself is the only reading that cannot be wrong.
    """
    found = []
    root = Path(root)
    if not root.is_dir():
        return found
    stack = [(root, 0)]
    while stack:
        directory, level = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name == ".remember":
                # remember 自己會把 .remember 做成指向 memory/journal/<slug> 的連結,
                # 所以名字要在略過連結之前判斷,不然這類專案永遠掃不到
                if not (entry.is_dir() or entry.is_symlink()):
                    continue
                # 家目錄不是專案,它是專案住的地方;資料庫更不是,
                # 認領它等於把筆記本收進自己的日誌裡
                if _path_identity(directory, memory_paths.HOME) or _path_identity(directory, memory_paths.SCRIPT_DIR):
                    continue
                found.append(directory)
                continue
            if not entry.is_dir() or entry.is_symlink():
                continue
            if entry.name in _SCAN_SKIP or level >= depth:
                continue
            stack.append((entry, level + 1))
    return sorted(set(found))


def unadopted_below(root: Path, depth: int = 4) -> list:
    """The projects a scan would still have something to do for."""
    return [
        project for project in journals_below(root, depth)
        if memory_journal.journal_state(project)[0] in {"local", "legacy", "rekey"}
    ]


_LINE_LIMIT = 64 * 1024


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _first_cwd(transcript: Path) -> "Path | None":
    with transcript.open(encoding="utf-8", errors="replace") as handle:
        for _ in range(50):
            # 一行可能是幾百 MB 的工具輸出:cwd 在每行開頭附近,讀前段就夠,其餘分段跳過
            line = handle.readline(_LINE_LIMIT)
            if not line:
                return None
            if not line.endswith("\n"):
                while True:
                    rest = handle.readline(_LINE_LIMIT)
                    if not rest or rest.endswith("\n"):
                        break
                continue
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                continue
            cwd = record.get("cwd") if isinstance(record, dict) else None
            if isinstance(cwd, str) and cwd and "\x00" not in cwd:
                return Path(cwd)
    return None


def _session_cwd(sessions: Path) -> "Path | None":
    """The working directory Claude Code recorded for one project's sessions."""
    try:
        # 只讀一般檔案:同名的 FIFO 會讓 open 一直卡住
        transcripts = [path for path in sessions.glob("*.jsonl") if path.is_file()]
    except OSError:
        return None
    for transcript in sorted(transcripts, key=_mtime, reverse=True)[:3]:
        try:
            cwd = _first_cwd(transcript)
        except OSError:
            continue
        if cwd is not None:
            return cwd
    return None


def _fold(name: str) -> str:
    # 只有 Windows 的路徑不分大小寫;remember 與 Claude Code 對磁碟代號的大小寫不一定相同
    return name.lower() if os.name == "nt" else name


def _usable_project(cwd: Path) -> bool:
    try:
        if not cwd.is_dir():
            return False
        # 經過連結的路徑 adopt 會拒絕;放進清單只會讓 adopt all 中途失敗
        if any(part.is_symlink() or is_reparse_point(part) for part in (cwd, *cwd.parents)):
            return False
    except (OSError, ValueError):
        return False
    # 家目錄與資料庫都不是專案(跟 journals_below 同一條規則)
    return not (
        _path_identity(cwd, memory_paths.HOME) or _path_identity(cwd, memory_paths.SCRIPT_DIR)
    )


def journals_from_sessions() -> list:
    """Projects with an unadopted journal that no directory scan reaches.

    A project on another drive (a Google Drive G:, a D: data disk) is never
    under home, so the nightly scan missed it: one on G: kept its journal
    on a single machine from July to November. The journal's own folder name
    cannot be decoded, but Claude Code's transcripts for the same name
    record the directory they ran in. Two paths that share one folder name
    (`a-b` and `a/b`) cannot be told apart here; the transcript decides.
    """
    root = memory_paths.journal_root()
    sessions_root = memory_paths.CLAUDE_HOME / "projects"
    try:
        journals = sorted(root.iterdir())
        sessions = {_fold(entry.name): entry for entry in sessions_root.iterdir()}
    except OSError:
        return []
    found = []
    for journal in journals:
        try:
            if journal.is_symlink() or is_reparse_point(journal) or not journal.is_dir():
                continue
        except OSError:
            continue
        sessions_dir = sessions.get(_fold(journal.name))
        cwd = _session_cwd(sessions_dir) if sessions_dir is not None else None
        if cwd is None or _fold(memory_paths.session_slug(cwd)) != _fold(journal.name):
            continue
        if _usable_project(cwd):
            found.append(cwd)
    return sorted(set(found))


def unadopted(root: "Path | None" = None) -> list:
    """unadopted_below(root or home), plus projects elsewhere that sessions name."""
    below = unadopted_below(root or memory_paths.HOME)
    elsewhere = [
        project for project in journals_from_sessions()
        if project not in below and memory_journal.journal_state(project)[0] == "local"
    ]
    return sorted({*below, *elsewhere})
