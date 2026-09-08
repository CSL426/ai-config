"""Read-only plans for memory lifecycle changes.

Plans inspect the same inputs as the mutations. They never execute a mutation
against temporary paths: journal collision names depend on the real directory.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import memory

ACTIONS = frozenset({"enable", "disable", "adopt", "release"})


@dataclass
class MemoryPlan:
    action: str
    project: Path | None
    changes: list[dict] = field(default_factory=list)
    relevant_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    relevant_values: dict = field(default_factory=dict)

    def change(
        self, operation: str, destination: Path, reason: str, *,
        source: Path | None = None, target: Path | None = None,
        tool: str = "all", shared: bool = False,
    ) -> None:
        physical = target or destination
        self.changes.append({
            "category": "memory", "tool": tool, "operation": operation,
            "source": str(source) if source is not None else None,
            "destination": str(destination), "physical_target": str(physical),
            "shared": shared, "reason": reason,
        })
        self.relevant_paths.extend(
            path for path in (source, destination, physical) if path is not None
        )


def _text_change(
    result: MemoryPlan, path: Path, updated: str | None, reason: str, *,
    tool: str = "all", target: Path | None = None,
) -> None:
    actual = target or path
    current = memory._read_text(actual) if actual.exists() else None
    if current == updated:
        return
    operation = "delete" if updated is None else "modify" if current is not None else "add"
    result.change(
        operation, path, reason, target=actual, tool=tool,
        shared=actual != path, source=actual if current is not None else None,
    )


def _ignore_change(result: MemoryPlan) -> None:
    path = memory.memory_dir() / ".gitignore"
    current = memory._read_text(path)
    lines = [line for line in current.splitlines() if line != "journal/"]
    if "/journal/" not in lines:
        lines.append("/journal/")
        _text_change(result, path, "\n".join(lines) + "\n", "排除本機日誌與連結")


def _index_and_link(result: MemoryPlan) -> None:
    if not memory.index_path().is_file():
        _text_change(result, memory.index_path(), memory.INDEX_TEMPLATE, "建立共用記憶索引")
        topics = memory.memory_dir() / memory.TOPICS_NAME
        if not topics.exists():
            result.change("mkdir", topics, "建立全域主題目錄")
    if memory.link_state()[0] == "missing":
        result.change(
            "link", memory.MEMORY_LINK, "三個工具共用的記憶入口",
            source=memory.memory_dir(), target=memory.memory_dir(), shared=True,
        )


def _journal_config(result: MemoryPlan, *, remove: bool = False) -> None:
    import json

    state, _value = memory.journal_config_state()
    config = memory._read_user_config() or {}
    if remove:
        if state != "ours":
            return
        config.pop("data_dir", None)
        updated = json.dumps(config, ensure_ascii=False, indent=2) + "\n" if config else None
    else:
        if state == "ours":
            return
        config["data_dir"] = memory.JOURNAL_TEMPLATE
        updated = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    _text_change(result, memory.REMEMBER_USER_CONFIG, updated, "更新 remember data_dir", tool="claude")


def _rules(result: MemoryPlan, *, enabling: bool) -> None:
    for path in [*memory.instruction_paths(), memory.agy_rules_path()]:
        target = memory.rules_target(path)
        current = memory._read_text(target)
        if enabling:
            updated = memory.with_block(current)
        elif not memory.has_block(current):
            continue
        else:
            updated = memory.without_block(current)
            if path == memory.agy_rules_path() and not updated.strip():
                updated = None
        tool = "agy" if path == memory.agy_rules_path() else "codex" if path.name == "AGENTS.md" else "claude"
        reason = "安裝 acg 管理規則" if enabling else "移除 acg 管理規則，保留其他內容"
        if target != path:
            reason += "；此檔案亦由 Claude 使用"
        if path.is_relative_to(memory.SCRIPT_DIR):
            reason += "；修改資料來源，不包含在 memory push"
        _text_change(result, path, updated, reason, tool=tool, target=target)


def _journal(result: MemoryPlan) -> None:
    assert result.project is not None
    root = result.project
    link = memory.journal_link(root)
    target = memory.project_journal_dir(memory.project_key(root))
    legacy = root / ".remember"
    result.relevant_paths.extend([link, target, legacy])
    state, detail = memory.journal_state(root)
    if state == "foreign":
        raise RuntimeError(f"日誌連結已指向別處:{detail}")
    memory._check_journal_tree(target)
    if result.action == "release":
        if state != "adopted":
            return
        result.change("unlink", link, "移除共用日誌連結", target=target)
        result.change("mkdir", link, "日誌改存本機普通目錄")
        if target.exists():
            for entry in sorted(target.iterdir()):
                result.change("move", link / entry.name, "搬回本機；資料庫產生 Git 刪除差異", source=entry)
        result.warnings.append("後續提交並同步會移除資料庫日誌；其他機器既有連結將受 pull 影響。Git 歷史保留。")
        return
    if state == "adopted":
        return
    memory._check_journal_tree(link)
    memory._check_journal_tree(legacy)
    if not target.exists():
        result.change("mkdir", target, "建立可同步的專案日誌")
    # Track virtual destinations so later sources get exactly the same collision
    # suffixes as sequential real moves, including incoming .gitignore files.
    occupied = {p.name: p for p in target.iterdir()} if target.exists() else {}

    def destination(name: str, origin: str) -> Path:
        candidate, number = name, 0
        while candidate in occupied:
            number += 1
            suffix = "" if number == 1 else f"-{number}"
            candidate = f"{name}.from-{origin}{suffix}"
        return target / candidate

    sources = [link] if state == "local" else []
    migrate = legacy.is_dir() and not (legacy / memory.MIGRATED_NOTE).is_file()
    if migrate:
        sources.append(legacy)
    for source in sources:
        for entry in sorted(source.iterdir()):
            if source == legacy and entry.name in (".gitignore", memory.MIGRATED_NOTE):
                continue
            dest = destination(entry.name, source.parent.name)
            occupied[dest.name] = entry
            result.change("move", dest, "搬入可同步日誌；同名版本保留" if dest.name != entry.name else "搬入可同步日誌", source=entry)
        if source == link:
            result.change("rmdir", link, "移除搬空的本機日誌目錄")
    ignore = target / ".gitignore"
    prior_ignore = occupied.get(".gitignore")
    if prior_ignore is not None and prior_ignore.read_bytes() != memory.JOURNAL_GITIGNORE.encode():
        dest = destination(".gitignore", "journal")
        result.change("move", dest, "保留原日誌忽略設定", source=ignore)
        result.change("add", ignore, "寫入日誌同步忽略設定")
    elif prior_ignore is None:
        result.change("add", ignore, "寫入日誌同步忽略設定")
    _ignore_change(result)
    if migrate:
        note = legacy / memory.MIGRATED_NOTE
        text = f"Memory data migrated to:\n  {target}\nThis directory is now empty; you may delete it.\n"
        _text_change(result, note, text, "記錄舊日誌搬移目的地")
    result.change("link", link, "remember 繼續透過本機入口寫入專案日誌", source=target, target=target)


def plan(action: str, project: Path | None = None) -> MemoryPlan:
    """Validate and describe an operation without changing any filesystem state."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown memory action: {action}")
    if action in {"adopt", "release"}:
        if project is None:
            raise ValueError("adopt/release requires an explicit project path")
        memory.assert_plain_path(project, directory=True)
        if not project.is_dir():
            raise ValueError(f"Project directory does not exist: {project}")
        project = memory.project_root(project)
        memory.assert_plain_path(project, directory=True)
    elif project is not None:
        raise ValueError("enable/disable does not accept a project")
    memory.preflight_memory()
    memory.preflight_rules(enabling=action == "enable")
    state, detail = memory.link_state()
    if state not in {"ok", "missing"}:
        raise RuntimeError(f"共用記憶連結衝突:{detail}")
    config_state, config_value = memory.journal_config_state()
    if action in {"enable", "adopt"} and config_state == "other":
        raise RuntimeError(f"remember 的 data_dir 已另外設定為 {config_value}")
    result = MemoryPlan(action, project)
    result.relevant_values = {"remember_installed": memory.remember_installed()}
    result.relevant_paths = [
        memory.memory_dir(), memory.MEMORY_LINK,
        *memory.instruction_paths(), memory.source_rules_path(),
        memory.SCRIPT_DIR / "codex" / "AGENTS.md", memory.agy_rules_path(),
        memory.codex_override_path(), memory.REMEMBER_USER_CONFIG,
    ]
    result.relevant_paths.extend(memory.rules_target(path) for path in memory.instruction_paths())
    if action == "enable":
        _index_and_link(result)
        _rules(result, enabling=True)
        _ignore_change(result)
        if memory.remember_installed():
            _journal_config(result)
        else:
            result.warnings.append("remember 未安裝；不變更日誌設定，也不安裝外掛。")
        result.warnings.append("規則已安裝後，請開新會話驗證；agy CLI 載入仍須實際驗收。")
        if memory.codex_override_path().is_file():
            result.warnings.append("AGENTS.override.md 會遮蔽 Codex 的共用規則。")
        if not memory.source_rules_path().is_file():
            result.warnings.append("資料庫沒有 claude/CLAUDE.md，無法同步該規則來源。")
    elif action == "disable":
        _rules(result, enabling=False)
        if state == "ok":
            result.change("unlink", memory.MEMORY_LINK, "移除共用入口，保留資料", target=memory.memory_dir(), shared=True)
        _journal_config(result, remove=True)
        result.warnings.append("記憶內容保留；已搬移的日誌不搬回本機。")
    else:
        if action == "adopt":
            if not memory.remember_installed():
                raise RuntimeError("找不到 remember plugin，無法接管日誌")
            _index_and_link(result)
            _journal_config(result)
        _journal(result)
        if project is not None and not memory.project_key(project).stable:
            result.warnings.append("專案鍵值依本機目錄名稱，跨機器一致性未保證。")
    result.relevant_paths = list(dict.fromkeys(result.relevant_paths))
    return result
