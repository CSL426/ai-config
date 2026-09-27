"""The notebook's index, its drift and secret scans, and memory status."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import memory_journal, memory_paths
from .links import _path_identity
from .safety import is_reparse_point, looks_like_secret

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
    """Notes carrying something that must not be committed.

    acg never writes the notes itself — an AI session does, straight to
    disk. push already refuses to commit a secret, but that is the last
    gate; saying so at status time means the note can be fixed before
    it is ever staged.
    """
    root = memory_paths.memory_dir()
    if not root.is_dir():
        return []
    found = []
    # 只看會同步出去的部分。本機日誌被 memory/.gitignore 排除,永遠不會
    # 離開這台機器,把它報成外洩風險只會製造假警報
    scanned = [root / memory_paths.INDEX_NAME, root / memory_paths.TOPICS_NAME, root / memory_paths.PROJECTS_NAME]
    candidates = sorted(
        path
        for base in scanned
        for path in ([base] if base.is_file() else base.rglob("*.md"))
    )
    for path in candidates:
        if is_reparse_point(path) or not path.is_file():
            continue
        if memory_paths.JOURNAL_DIR_NAME in path.relative_to(root).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if looks_like_secret(text):
            found.append(path.relative_to(root).as_posix())
    return found


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
            if not entry.is_dir() or entry.is_symlink():
                continue
            if entry.name == ".remember":
                # 家目錄不是專案,它是專案住的地方;資料庫更不是,
                # 認領它等於把筆記本收進自己的日誌裡
                if memory_journal.journal_link(directory) is None:
                    continue
                if _path_identity(directory, memory_paths.HOME) or _path_identity(directory, memory_paths.SCRIPT_DIR):
                    continue
                found.append(directory)
                continue
            if entry.name in _SCAN_SKIP or level >= depth:
                continue
            stack.append((entry, level + 1))
    return sorted(set(found))


def unadopted_below(root: Path, depth: int = 4) -> list:
    """The projects a scan would still have something to do for."""
    return [
        project for project in journals_below(root, depth)
        if memory_journal.journal_state(project)[0] in {"local", "legacy"}
    ]
