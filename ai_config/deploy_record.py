"""What `deploy` put into a project, so leaving the machine can take it back out.

The record lives in the project itself (.acg-deploy.json). A project can
belong to someone else, so every path read back from it is treated as
untrusted: it must stay inside the project and pass the same reparse
point checks as any other write before anything is removed.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .memory_paths import PROJECT_BLOCK_BEGIN, PROJECT_BLOCK_END
from .safety import is_reparse_point

RECORD_NAME = ".acg-deploy.json"
RULES_FILE = "AGENTS.md"
_PROJECT_BLOCK = re.compile(
    re.escape(PROJECT_BLOCK_BEGIN) + r".*?" + re.escape(PROJECT_BLOCK_END) + r"\n?",
    re.DOTALL,
)


@dataclass
class Record:
    # 專案內相對路徑 → 放進去時的 sha256;只有內容沒被改過才能收回
    files: dict[str, str] = field(default_factory=dict)
    plugins: list[str] = field(default_factory=list)
    marketplaces: list[str] = field(default_factory=list)
    # None:沒有加記憶規則;否則記下 AGENTS.md 是不是 acg 建的
    rules_created: "bool | None" = None
    # 第一次動 plugin 前專案 settings.json 的原文;None 表示原本沒有這個檔
    settings_before: "str | None" = None
    settings_saved: bool = False

    def empty(self) -> bool:
        return (
            not (self.files or self.plugins or self.marketplaces)
            and self.rules_created is None
        )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(project: Path) -> Record:
    path = project / RECORD_NAME
    if not path.is_file() or is_reparse_point(path):
        return Record()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{RECORD_NAME} 讀不懂,不敢照它刪東西:{exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"{RECORD_NAME} 格式不對,不敢照它刪東西")  # noqa: TRY004 — 呼叫端只接 RuntimeError
    files = raw.get("files") if isinstance(raw.get("files"), dict) else {}
    created = raw.get("rules_created")
    settings = raw.get("claude_settings")
    saved = isinstance(settings, dict) and isinstance(settings.get("existed"), bool)
    before = settings.get("text") if saved and settings["existed"] else None
    return Record(
        settings_before=before if isinstance(before, str) else None,
        settings_saved=saved,
        files={str(k): str(v) for k, v in files.items()},
        plugins=[str(p) for p in raw.get("plugins") or [] if isinstance(p, str)],
        marketplaces=[str(m) for m in raw.get("marketplaces") or [] if isinstance(m, str)],
        rules_created=created if isinstance(created, bool) else None,
    )


def save(project: Path, record: Record) -> None:
    path = project / RECORD_NAME
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point record: {path}")
    if record.empty():
        if path.is_file():
            path.unlink()
        return
    body = {
        "files": dict(sorted(record.files.items())),
        "plugins": sorted(set(record.plugins)),
        "marketplaces": sorted(set(record.marketplaces)),
        "rules_created": record.rules_created,
    }
    if record.settings_saved:
        body["claude_settings"] = {
            "existed": record.settings_before is not None,
            "text": record.settings_before,
        }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inside(project: Path, relative: str) -> "Path | None":
    """The project path a record entry names, or None when it points elsewhere."""
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        return None
    current = project
    for part in candidate.parts:
        current = current / part
        if is_reparse_point(current):
            return None
    resolved = current.resolve()
    if not resolved.is_relative_to(project.resolve()):
        return None
    return current


def put_rules(project: Path, block: str) -> tuple[str, bool]:
    """Add or refresh acg's block in AGENTS.md; returns (status, created)."""
    path = project / RULES_FILE
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point destination: {path}")
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return "placed", True
    text = path.read_text(encoding="utf-8")
    found = _PROJECT_BLOCK.findall(text)
    if len(found) > 1:
        raise RuntimeError(f"{RULES_FILE} 裡有不只一段 acg 記憶規則,先手動整理")
    if found and found[0].rstrip("\n") == block.rstrip("\n"):
        return "ready", False
    if found:
        updated = _PROJECT_BLOCK.sub(lambda _: block, text, count=1)
    else:
        updated = text + ("" if text.endswith("\n") or not text else "\n") + "\n" + block
    path.write_text(updated, encoding="utf-8")
    return "placed", False


def drop_rules(project: Path, created: bool) -> None:
    path = project / RULES_FILE
    if not path.is_file() or is_reparse_point(path):
        return
    text = path.read_text(encoding="utf-8")
    remaining = _PROJECT_BLOCK.sub("", text)
    if created and not remaining.strip():
        path.unlink()
        return
    if remaining != text:
        path.write_text(remaining.rstrip("\n") + "\n" if remaining.strip() else "", encoding="utf-8")


def prune_empty_parents(project: Path, path: Path) -> None:
    """Remove directories deploy left empty, never the project itself."""
    root = project.resolve()
    current = path.parent
    while current.resolve() != root and current.resolve().is_relative_to(root):
        if is_reparse_point(current) or not current.is_dir() or any(current.iterdir()):
            return
        current.rmdir()
        current = current.parent


SETTINGS_FILE = ".claude/settings.json"


def remember_settings(project: Path, record: Record) -> None:
    """Keep the project's settings.json as it was before any plugin touched it."""
    if record.settings_saved:
        return
    path = project / SETTINGS_FILE
    record.settings_before = path.read_text(encoding="utf-8") if path.is_file() else None
    record.settings_saved = True


def restore_settings(project: Path, record: Record) -> None:
    """Undo the reformatting and empty enabledPlugins Claude leaves after uninstall.

    Only when nothing else changed: anyone's later edits stay.
    """
    path = project / SETTINGS_FILE
    if not record.settings_saved or not path.is_file() or is_reparse_point(path):
        return
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        original = (
            json.loads(record.settings_before) if record.settings_before is not None else {}
        )
    except ValueError:
        return
    if not isinstance(current, dict) or not isinstance(original, dict):
        return
    if current.get("enabledPlugins") == {} and "enabledPlugins" not in original:
        current.pop("enabledPlugins")
    if current != original:
        return
    if record.settings_before is None:
        path.unlink()
        prune_empty_parents(project, path)
    else:
        path.write_text(record.settings_before, encoding="utf-8")
