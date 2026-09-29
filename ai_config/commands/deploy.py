"""Install chosen skills, plugins and rules into one project instead of globally.

On someone else's machine the global homes belong to its owner. `deploy`
puts what you pick where each tool reads it inside the project, and only
there. See docs/project-deploy-spec.md.
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import deploy_record
from ..console import (
    BOLD,
    CYAN,
    NC,
    ask,
    log_error,
    log_header,
    log_info,
    log_success,
    log_warn,
)
from ..console import (
    confirm as confirm_prompt,
)
from ..fsops import is_excluded, safe_cp
from ..memory_paths import project_rules_block
from ..paths import MEMORY_DIR_NAME, SCRIPT_DIR
from ..profiles import (
    PROFILES_NAME,
    load_profiles,
    save_profile,
    valid_profile_name,
)
from ..safety import assert_no_symlinks
from ..subproc import UTF8

# 資料庫來源 → 專案內的位置;每個位置是哪些工具會讀,是實測出來的
SKILL_SOURCES = (
    ("skills", ".claude/skills", "Claude"),
    ("shared/both", ".agents/skills", "Codex, agy"),
    ("shared/codex", ".codex/skills", "Codex"),
    ("shared/agy", ".agent/skills", "agy"),
)
MERGED_DIRS = ("rules", "agents", "commands")


@dataclass(frozen=True)
class Item:
    """One menu row; `name` is also what a profile stores."""

    name: str
    note: str
    # (來源, 專案內相對路徑)
    placements: tuple[tuple[Path, str], ...] = ()
    plugin: str = ""
    merge: bool = False
    memory: bool = False


@dataclass
class Outcome:
    placed: list[str] = field(default_factory=list)
    ready: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _children(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        child.name for child in directory.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def _plugins(source: Path) -> dict[str, bool]:
    try:
        settings = json.loads((source / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plugins = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    if not isinstance(plugins, dict):
        return {}
    return {str(key): value is True for key, value in sorted(plugins.items())}


def _available_items(source: Path) -> list[Item]:
    items: list[Item] = []
    if (source / "CLAUDE.md").is_file():
        items.append(Item("CLAUDE.md", "Claude",
                          ((source / "CLAUDE.md", ".claude/CLAUDE.md"),)))
    for name in MERGED_DIRS:
        if (source / name).is_dir():
            items.append(Item(name, "Claude",
                              ((source / name, f".claude/{name}"),), merge=True))
    skills: dict[str, list[tuple[Path, str, str]]] = {}
    for relative, target, readers in SKILL_SOURCES:
        for skill in _children(source / relative):
            skills.setdefault(skill, []).append(
                (source / relative / skill, f"{target}/{skill}", readers)
            )
    for skill, spots in sorted(skills.items()):
        readers = ", ".join(dict.fromkeys(
            reader for _, _, group in spots for reader in group.split(", ")
        ))
        items.append(Item(
            f"skills/{skill}", readers,
            tuple((src, dst) for src, dst, _ in spots),
        ))
    for plugin, enabled in _plugins(source).items():
        note = "Claude plugin" + ("" if enabled else ",全域沒開")
        items.append(Item(f"plugins/{plugin}", note, plugin=plugin))
    if _memory_root(source).is_dir():
        items.append(Item("memory", "Claude, Codex, agy", memory=True))
    return items


def _memory_root(source: Path) -> Path:
    return source.parent / MEMORY_DIR_NAME


def _files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not is_excluded(path)
    }


def _place_file(src: Path, dst: Path) -> str:
    if dst.is_symlink() or dst.exists():
        same = dst.is_file() and not dst.is_symlink() and dst.read_bytes() == src.read_bytes()
        return "ready" if same else "conflict"
    safe_cp(src, dst)
    return "placed"


def _place_tree(src: Path, dst: Path) -> str:
    """A skill travels whole: never half old and half new."""
    assert_no_symlinks(src)
    if dst.is_symlink() or dst.exists():
        if dst.is_symlink() or not dst.is_dir():
            return "conflict"
        assert_no_symlinks(dst)
        return "ready" if _files(dst) == _files(src) else "conflict"
    for relative in _files(src):
        safe_cp(src / relative, dst / relative)
    return "placed"


def _merge_tree(src: Path, dst: Path, outcome: Outcome, label: str) -> None:
    """Rules, agents and commands join what the project already has, file by file."""
    assert_no_symlinks(src)
    if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
        outcome.conflicts.append(label)
        return
    assert_no_symlinks(dst)
    for relative in _files(src):
        result = _place_file(src / relative, dst / relative)
        _record(outcome, result, f"{label}/{relative.as_posix()}")


def _record(outcome: Outcome, result: str, label: str) -> None:
    {"placed": outcome.placed, "ready": outcome.ready,
     "conflict": outcome.conflicts}[result].append(label)


def _claude_binary() -> "str | None":
    from .update import _claude_binary as find

    return find()


def _run_claude(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    claude = _claude_binary()
    if claude is None:
        raise RuntimeError("找不到 claude 指令")
    return subprocess.run(
        [claude, "plugin", *args], cwd=cwd,
        capture_output=True, text=True, **UTF8, timeout=300, check=False,
    )


def _marketplace_source(source: Path, marketplace: str) -> "str | None":
    try:
        settings = json.loads((source / "settings.json").read_text(encoding="utf-8"))
        entry = settings["extraKnownMarketplaces"][marketplace]["source"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(entry, dict):
        return None
    kind = entry.get("source")
    value = {"github": entry.get("repo"), "git": entry.get("url"),
             "url": entry.get("url"), "directory": entry.get("path")}.get(kind)
    return value if isinstance(value, str) and value else None


def _project_plugins(project: Path) -> dict:
    try:
        settings = json.loads(
            (project / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    plugins = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def _last_line(done: subprocess.CompletedProcess) -> str:
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit {done.returncode}"


def _install_plugin(
    source: Path, project: Path, plugin: str, record: deploy_record.Record,
) -> str:
    """Enable a plugin for this project only; the host's own settings stay put."""
    if _project_plugins(project).get(plugin) is True:
        return "ready"
    deploy_record.remember_settings(project, record)
    install = ["install", plugin, "--scope", "project"]
    done = _run_claude(install, project)
    if done.returncode != 0 and "@" in plugin:
        # 主機不認得這個 marketplace;宣告在專案裡,不動主機的清單
        marketplace = plugin.rsplit("@", 1)[1]
        where = _marketplace_source(source, marketplace)
        if where is not None:
            added = _run_claude(["marketplace", "add", where, "--scope", "project"], project)
            if added.returncode == 0:
                record.marketplaces.append(marketplace)
                done = _run_claude(install, project)
    if done.returncode != 0:
        raise RuntimeError(_last_line(done))
    record.plugins.append(plugin)
    return "placed"


def _remember_files(project: Path, record: deploy_record.Record, placed: list[str]) -> None:
    for relative in placed:
        path = project / relative
        targets = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for target in targets:
            record.files[target.relative_to(project).as_posix()] = deploy_record.digest(target)


def _deploy(source: Path, project: Path, items: list[Item], selection: list[int]) -> Outcome:
    outcome = Outcome()
    record = deploy_record.load(project)
    try:
        for index in selection:
            item = items[index]
            if item.plugin:
                try:
                    result = _install_plugin(source, project, item.plugin, record)
                    _record(outcome, result, item.name)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    outcome.failed.append(f"{item.name}:{exc}")
                continue
            if item.memory:
                block = project_rules_block(_memory_root(source))
                result, created = deploy_record.put_rules(project, block)
                if record.rules_created is None:
                    record.rules_created = created
                _record(outcome, result, f"{deploy_record.RULES_FILE}(記憶規則)")
                continue
            before = len(outcome.placed)
            for src, relative in item.placements:
                if item.merge:
                    _merge_tree(src, project / relative, outcome, relative)
                elif src.is_dir():
                    _record(outcome, _place_tree(src, project / relative), relative)
                else:
                    _record(outcome, _place_file(src, project / relative), relative)
            _remember_files(project, record, outcome.placed[before:])
    finally:
        # 放到一半失敗也要記下已經放的,不然撤除時找不回來
        deploy_record.save(project, record)
    return outcome


def _report(outcome: Outcome, project: Path) -> int:
    for label in outcome.placed:
        log_success(label)
    if outcome.ready:
        log_info(f"已經在專案裡、內容相同:{len(outcome.ready)} 項")
    for label in outcome.conflicts:
        log_warn(f"專案裡已有不同內容,沒有覆蓋:{label}")
    for label in outcome.failed:
        log_error(f"沒有裝上:{label}")
    if outcome.conflicts:
        log_info("要換成資料庫的版本,先刪掉專案裡那一份再跑一次")
    if outcome.conflicts or outcome.failed:
        return 1
    log_success(f"已部署到 {project};只在這個目錄生效,家目錄沒有改動")
    log_info(f"放了什麼記在 {deploy_record.RECORD_NAME};離開這台時用 deploy --remove 收回")
    return 0


def _parse_selection(raw: str, total: int) -> "list[int] | None":
    """Indices chosen by the user, or None when the input is unusable."""
    entry = raw.strip().lower()
    if not entry:
        return None
    if entry in {"a", "all"}:
        return list(range(total))

    chosen: set[int] = set()
    for part in entry.replace(",", " ").split():
        if "-" in part[1:]:
            start, _, end = part.partition("-")
            if not (start.isdigit() and end.isdigit()):
                return None
            lo, hi = int(start), int(end)
            if lo > hi:
                lo, hi = hi, lo
            values = range(lo, hi + 1)
        elif part.isdigit():
            values = [int(part)]
        else:
            return None
        for value in values:
            if not 1 <= value <= total:
                return None
            chosen.add(value - 1)
    return sorted(chosen) or None


def _resolve_profile(items: list[Item], wanted: list[str]) -> "list[int] | None":
    """Map a profile's stored names onto current menu indices."""
    by_name = {item.name: index for index, item in enumerate(items)}
    missing = [name for name in wanted if name not in by_name]
    if missing:
        log_error(f"Profile refers to items no longer in the repo: {', '.join(missing)}")
        return None
    return sorted(by_name[name] for name in wanted)


def _destinations(item: Item) -> str:
    if item.plugin:
        return "claude plugin install --scope project"
    if item.memory:
        return f"{deploy_record.RULES_FILE} 裡加一段 acg 區塊"
    return ", ".join(relative + ("/" if src.is_dir() else "") for src, relative in item.placements)


def run_deploy(
    target: "str | None",
    profile: "str | None" = None,
    save_as: "str | None" = None,
) -> int:
    source = SCRIPT_DIR / "claude"
    if not source.is_dir():
        log_error(f"No Claude configuration in the data repository: {source}")
        return 1
    project = Path(target).expanduser() if target else Path.cwd()
    if not project.is_dir():
        log_error(f"Target is not a directory: {project}")
        return 1
    project = project.resolve()
    items = _available_items(source)
    if not items:
        log_error("The data repository has nothing to deploy")
        return 1
    if save_as is not None and not valid_profile_name(save_as):
        log_error(f"Invalid profile name: {save_as}")
        return 1

    if profile is not None:
        profiles = load_profiles(source)
        if profile not in profiles:
            known = ", ".join(sorted(profiles)) or "(none defined)"
            log_error(f"Unknown profile: {profile}")
            log_info(f"Available profiles: {known}")
            return 1
        selection = _resolve_profile(items, profiles[profile])
        if selection is None:
            return 1
        log_header(f"Deploy profile '{profile}' to {project}")
        return _report(_deploy(source, project, items, selection), project)

    log_header(f"Deploy to {project}")
    for number, item in enumerate(items, start=1):
        print(f"  {CYAN}{number:>2}{NC}  {item.name}  ({item.note})")
    print()
    print(f"  Select items: numbers (1 3), a range (1-3), or {BOLD}a{NC} for all")

    answer = ask("  > ")
    selection = None if answer is None else _parse_selection(answer, len(items))
    if selection is None:
        log_info("Nothing selected; deploy cancelled")
        return 0

    print()
    for index in selection:
        print(f"  {items[index].name} → {_destinations(items[index])}")
    print()
    log_info("不刪也不覆蓋專案裡已有的檔案;內容不同的會略過並列出")

    if not confirm_prompt("\n  Deploy these items? [y/N] "):
        log_info("Cancelled; nothing was written")
        return 0

    outcome = _deploy(source, project, items, selection)
    if save_as is not None:
        save_profile(source, save_as, [items[index].name for index in selection])
        log_success(f"Saved profile '{save_as}' to {PROFILES_NAME}")
    return _report(outcome, project)


def _removal_plan(project: Path, record: deploy_record.Record) -> tuple[list[Path], list[str]]:
    """Files still exactly as deploy left them, and what cannot be taken back safely."""
    removable: list[Path] = []
    kept: list[str] = []
    for relative, expected in sorted(record.files.items()):
        path = deploy_record.inside(project, relative)
        if path is None:
            kept.append(f"{relative}(不在專案裡,不處理)")
        elif not path.exists():
            continue
        elif not path.is_file() or deploy_record.digest(path) != expected:
            kept.append(f"{relative}(放進去之後被改過)")
        else:
            removable.append(path)
    return removable, kept


def run_undeploy(target: "str | None") -> int:
    project = Path(target).expanduser() if target else Path.cwd()
    if not project.is_dir():
        log_error(f"Target is not a directory: {project}")
        return 1
    project = project.resolve()
    try:
        record = deploy_record.load(project)
    except RuntimeError as exc:
        log_error(str(exc))
        return 1
    if record.empty():
        log_info(f"{project} 沒有 acg 部署紀錄,沒有東西要收回")
        return 0

    removable, kept = _removal_plan(project, record)
    log_header(f"Remove what deploy put into {project}")
    for path in removable:
        print(f"  刪除 {path.relative_to(project).as_posix()}")
    for plugin in record.plugins:
        print(f"  移除 plugin {plugin}(專案範圍)")
    for marketplace in record.marketplaces:
        print(f"  移除 marketplace 宣告 {marketplace}(專案範圍)")
    if record.rules_created is not None:
        print(f"  拿掉 {deploy_record.RULES_FILE} 裡的 acg 記憶規則")
    for label in kept:
        log_warn(f"保留:{label}")
    if not confirm_prompt("\n  Remove these? [y/N] "):
        log_info("Cancelled; nothing was removed")
        return 0
    return remove(project, record)


def remove(project: Path, record: deploy_record.Record) -> int:
    """Take back what the record lists; the part both the CLI and the GUI run."""
    removable, kept = _removal_plan(project, record)
    failed: list[str] = []
    remaining = deploy_record.Record()
    for path in removable:
        path.unlink()
        deploy_record.prune_empty_parents(project, path)
    for relative in record.files:
        path = deploy_record.inside(project, relative)
        if path is not None and path.exists():
            remaining.files[relative] = record.files[relative]
    for plugin in record.plugins:
        try:
            done = _run_claude(["uninstall", plugin, "--scope", "project"], project)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            failed.append(f"plugin {plugin}:{exc}")
            remaining.plugins.append(plugin)
            continue
        if done.returncode != 0:
            failed.append(f"plugin {plugin}:{_last_line(done)}")
            remaining.plugins.append(plugin)
    for marketplace in record.marketplaces:
        try:
            done = _run_claude(["marketplace", "remove", marketplace, "--scope", "project"], project)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            failed.append(f"marketplace {marketplace}:{exc}")
            remaining.marketplaces.append(marketplace)
            continue
        if done.returncode != 0:
            failed.append(f"marketplace {marketplace}:{_last_line(done)}")
            remaining.marketplaces.append(marketplace)
    if record.rules_created is not None:
        deploy_record.drop_rules(project, record.rules_created)
    if not remaining.plugins and not remaining.marketplaces:
        deploy_record.restore_settings(project, record)
    else:
        remaining.settings_before = record.settings_before
        remaining.settings_saved = record.settings_saved
    deploy_record.save(project, remaining)

    for label in failed:
        log_error(f"沒有收回:{label}")
    if kept:
        log_info(f"被改過的檔案留著沒刪,紀錄仍在 {deploy_record.RECORD_NAME}")
    if failed or kept:
        return 1
    log_success("已收回 deploy 放進這個專案的東西")
    return 0


# ---- GUI 用:不問問題、不印選單的版本 --------------------------------------

_READERS = {".claude": "Claude", ".agents": "Codex, agy", ".codex": "Codex", ".agent": "agy"}


def source_dir() -> Path:
    return SCRIPT_DIR / "claude"


def items() -> list[Item]:
    source = source_dir()
    return _available_items(source) if source.is_dir() else []


def kind(item: Item) -> str:
    if item.plugin:
        return "plugin"
    if item.memory:
        return "memory"
    return "skill" if item.name.startswith("skills/") else "claude"


def select(names: list[str]) -> tuple[list[Item], list[int]]:
    available = items()
    by_name = {item.name: index for index, item in enumerate(available)}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"資料庫裡已經沒有:{', '.join(missing)}")
    if not names:
        raise ValueError("請至少選一項")
    return available, sorted({by_name[name] for name in names})


def _change(operation: str, tool: str, destination: Path, reason: str,
            source: "Path | None" = None) -> dict:
    return {
        "category": "deploy", "tool": tool, "operation": operation,
        "source": str(source) if source else None, "destination": str(destination),
        "physical_target": None, "shared": False, "reason": reason,
    }


def _file_change(src: Path, dst: Path, tool: str) -> dict:
    if not (dst.is_symlink() or dst.exists()):
        return _change("create", tool, dst, "新增到專案", src)
    same = dst.is_file() and not dst.is_symlink() and dst.read_bytes() == src.read_bytes()
    return _change("skip", tool, dst, "已在專案裡,內容相同" if same
                   else "專案裡已有不同內容,不覆蓋", src)


def preview(project: Path, names: list[str]) -> list[dict]:
    """What deploying `names` would do, without writing anything."""
    source = source_dir()
    available, selection = select(names)
    changes: list[dict] = []
    for index in selection:
        item = available[index]
        if item.plugin:
            target = project / deploy_record.SETTINGS_FILE
            if _project_plugins(project).get(item.plugin) is True:
                changes.append(_change("skip", "Claude", target, f"{item.plugin} 已在這個專案啟用"))
            else:
                changes.append(_change("install", "Claude", target,
                                       f"以專案範圍安裝 {item.plugin}"))
            continue
        if item.memory:
            target = project / deploy_record.RULES_FILE
            block = project_rules_block(_memory_root(source))
            if not target.exists():
                operation, reason = "create", "新建,只含 acg 記憶規則"
            elif block.rstrip("\n") in target.read_text(encoding="utf-8"):
                operation, reason = "skip", "記憶規則已在裡面"
            else:
                operation, reason = "modify", "在檔尾加一段 acg 記憶規則,原內容不動"
            changes.append(_change(operation, "Claude, Codex, agy", target, reason))
            continue
        for src, relative in item.placements:
            tool = _READERS.get(relative.split("/", 1)[0], "Claude")
            dst = project / relative
            if item.merge:
                if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
                    changes.append(_change("skip", tool, dst, "專案裡已有同名的檔案,不覆蓋", src))
                    continue
                changes.extend(_file_change(src / rel, dst / rel, tool) for rel in _files(src))
            elif src.is_dir():
                status = _place_tree_status(src, dst)
                reason = {"create": "新增到專案", "ready": "已在專案裡,內容相同",
                          "conflict": "專案裡已有不同內容,不覆蓋"}[status]
                changes.append(_change("create" if status == "create" else "skip",
                                       tool, dst, reason, src))
            else:
                changes.append(_file_change(src, dst, tool))
    return changes


def _place_tree_status(src: Path, dst: Path) -> str:
    assert_no_symlinks(src)
    if not (dst.is_symlink() or dst.exists()):
        return "create"
    if dst.is_symlink() or not dst.is_dir():
        return "conflict"
    assert_no_symlinks(dst)
    return "ready" if _files(dst) == _files(src) else "conflict"


def execute(project: Path, names: list[str]) -> int:
    available, selection = select(names)
    return _report(_deploy(source_dir(), project, available, selection), project)


def removal_preview(project: Path) -> list[dict]:
    record = deploy_record.load(project)
    if record.empty():
        return []
    removable, kept = _removal_plan(project, record)
    changes = [_change("delete", "專案", path, "部署時放入,內容沒被改過") for path in removable]
    changes += [_change("skip", "專案", project / label.split("(", 1)[0], "保留:" + label)
                for label in kept]
    target = project / deploy_record.SETTINGS_FILE
    changes += [_change("uninstall", "Claude", target, f"移除專案範圍的 plugin {plugin}")
                for plugin in record.plugins]
    changes += [_change("uninstall", "Claude", target, f"移除專案裡的 marketplace 宣告 {name}")
                for name in record.marketplaces]
    if record.rules_created is not None:
        changes.append(_change("modify", "Claude, Codex, agy", project / deploy_record.RULES_FILE,
                               "拿掉 acg 記憶規則" + (",檔案是 acg 建的,沒其他內容就刪掉"
                                                      if record.rules_created else "")))
    return changes


def summary(project: Path) -> dict:
    """What deploy has already put into this project, for the GUI to show."""
    record = deploy_record.load(project)
    return {
        "files": len(record.files), "plugins": sorted(record.plugins),
        "memory": record.rules_created is not None,
        "paths": sorted(record.files),
    }
