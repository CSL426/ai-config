"""Review exact apply results in a disposable home, then replay checked changes.

The worker reuses the real adapters in another process: their module-level home
paths never change in the GUI process, and projection cannot touch live files.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import paths, review
from .safety import is_reparse_point


class StalePreview(RuntimeError):
    pass


class ApplyFailure(RuntimeError):
    def __init__(self, message, backup_path=None, recovery_required=False):
        super().__init__(message)
        self.backup_path = backup_path
        self.recovery_required = recovery_required


def _plain_parents(path: Path) -> None:
    for parent in path.parents:
        if is_reparse_point(parent):
            raise RuntimeError(f"Refusing reparse point parent: {parent}")


def _link(path: Path, record: dict) -> None:
    if record["kind"] == "junction":
        from .links import _try_create_junction

        if not _try_create_junction(Path(record["target"]), path):
            raise OSError(f"Cannot create Junction: {path}")
    else:
        path.symlink_to(record["target"], target_is_directory=True)


def _copy_tree(
    source: Path,
    target: Path,
    real_home: Path,
    shadow: Path,
    deferred_links: list[tuple[Path, dict]],
) -> None:
    """Copy one root into the shadow home; links are recorded, not created.

    A link often sorts before the directory it points at (agy's
    ``antigravity-cli/skills`` points at ``config/skills``), and a Junction
    cannot be created towards a target that does not exist yet. The caller
    creates every deferred link once all roots are copied.
    """
    record = review.node(source)
    kind = record["kind"]
    if kind == "missing":
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind in ("symlink", "junction"):
        raw = record["target"]
        absolute = Path(raw)
        if absolute.is_absolute():
            try:
                raw = str(shadow / absolute.relative_to(real_home))
            except ValueError as exc:
                raise RuntimeError(f"Link outside managed home: {source}") from exc
        deferred_links.append((target, {**record, "target": raw}))
    elif kind == "directory":
        target.mkdir(exist_ok=True)
        for child in source.iterdir():
            if child.name not in paths.EXCLUDED_FILES and child.name != ".git":
                _copy_tree(
                    child, target / child.name, real_home, shadow, deferred_links
                )
    else:
        shutil.copy2(source, target)


def _sources(tools: list[str], category: str) -> list[Path]:
    roots = []
    claude = paths.claude_source_dir()
    if category in ("all", "settings"):
        if "claude" in tools:
            roots += [claude / p for p in paths.CLAUDE_MANAGED_FILES]
            roots += [claude / p for p in ("rules", "agents", "commands")]
        if "codex" in tools:
            roots += [
                paths.SCRIPT_DIR / "codex" / p
                for p in ("AGENTS.md", "config.toml", "rules")
            ]
            roots += [claude / "CLAUDE.md", claude / "rules"]
        if "agy" in tools:
            roots += [
                paths.SCRIPT_DIR / "agy" / p
                for p in ("settings.json", "mcp_config.json")
            ]
            roots += [claude / "mcp.json", claude / "plugins"]
    if category in ("all", "skills"):
        roots.append(claude / "skills")
        for tool in tools:
            if tool != "claude":
                roots += [
                    paths.SCRIPT_DIR / tool / "skills",
                    claude / "agents",
                    claude / "shared" / "both",
                    claude / "shared" / tool,
                ]
    return list(dict.fromkeys(roots))


@dataclass
class ApplyPlan:
    temporary: Path
    tools: list[str]
    category: str
    before: dict[str, dict]
    after: dict[str, dict]
    files: dict[str, Path]
    changes: list[dict]
    relevant_paths: list[Path]
    identity: str
    warnings: list[str]

    def close(self):
        shutil.rmtree(self.temporary, ignore_errors=True)

    def current_identity(self):
        return review.fingerprint(
            self.relevant_paths, review.git_state(paths.SCRIPT_DIR)
        )


def plan(tools: list[str], category: str) -> ApplyPlan:
    from .backup import managed_destinations
    from .commands.apply import _HEADERS, _TOOLS
    from .instructionblocks import prepare_instruction_blocks
    from .links import preflight_windows_links
    from .safety import assert_tool_destinations_safe
    from .staging import staged_projections

    if not tools or any(tool not in paths.ALL_TOOLS for tool in tools):
        raise ValueError("Unknown tool")
    if category not in ("settings", "skills", "all"):
        raise ValueError("Unknown category")
    temporary = Path(tempfile.mkdtemp(prefix="acg-apply-review-"))
    shadow = temporary / "home"
    shadow.mkdir()
    try:
        sources = _sources(tools, category)
        source_identity = review.fingerprint(sources)
        with staged_projections(tools, _TOOLS, _HEADERS, category=category) as stages:
            assert_tool_destinations_safe(tools, stages, category=category)
            preflight_windows_links(tools, category=category)
            prepare_instruction_blocks(stages, category=category)
            destinations = managed_destinations(tools, stages, category=category)
            roots = list(dict.fromkeys(row[2] for row in destinations))
            # Include the shared alias itself as well as its physical destination.
            if "codex" in tools and category != "skills":
                roots.append(paths.CODEX_HOME / "AGENTS.md")
            roots = list(dict.fromkeys(roots))
            before = {}
            deferred_links: list[tuple[Path, dict]] = []
            for root in roots:
                _plain_parents(root)
                before.update(review.tree(root))
                target = shadow / root.relative_to(paths.HOME)
                if not os.path.lexists(target):
                    _copy_tree(root, target, paths.HOME, shadow, deferred_links)
            for link_path, record in deferred_links:
                if not os.path.lexists(link_path):
                    if record["kind"] == "junction":
                        # A target outside the copied roots only needs a
                        # placeholder in the disposable home, never live.
                        target = Path(record["target"])
                        if not target.is_absolute():
                            target = link_path.parent / target
                        target.resolve().relative_to(shadow.resolve())
                        target.mkdir(parents=True, exist_ok=True)
                    _link(link_path, record)
            saved_stages = {}
            for tool, stage in stages.items():
                target = temporary / "stages" / tool
                shutil.copytree(stage, target, symlinks=True)
                saved_stages[tool] = str(target)
        if source_identity != review.fingerprint(sources):
            raise StalePreview("來源在預覽時已有變動，請重試。")
        relevant = list(dict.fromkeys(sources + roots))
        identity = review.fingerprint(relevant, review.git_state(paths.SCRIPT_DIR))
        manifest = temporary / "worker.json"
        manifest.write_text(
            json.dumps(
                {
                    "home": str(shadow),
                    "stages": saved_stages,
                    "category": category,
                    "tools": tools,
                    "dedicated_codex_agents": (
                        paths.SCRIPT_DIR / "codex" / "AGENTS.md"
                    ).is_file(),
                }
            ),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "HOME": str(shadow),
            "USERPROFILE": str(shadow),
            "AI_CONFIG_REPO": str(temporary / "data"),
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        }
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += ["-m", "ai_config"]
        command += ["_apply-preview-worker", str(manifest)]
        completed = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr or completed.stdout)
        after, files = {}, {}
        for root in roots:
            target = shadow / root.relative_to(paths.HOME)
            for filename, record in review.tree(target).items():
                actual = str(paths.HOME / Path(filename).relative_to(shadow))
                if record["kind"] in ("symlink", "junction"):
                    record = {
                        **record,
                        "target": record["target"].replace(
                            str(shadow), str(paths.HOME)
                        ),
                    }
                # Ownership records contain machine-local absolute link targets.
                if (
                    Path(filename).name == ".ai-config-skills-state.json"
                    and record["kind"] == "file"
                ):
                    content = Path(filename).read_text(encoding="utf-8")
                    if (
                        str(shadow) in content
                        or str(shadow).replace("\\", "\\\\") in content
                    ):
                        value = json.loads(content)
                        from .tools.agy import _replace_json_path

                        value = _replace_json_path(value, str(shadow), str(paths.HOME))
                        Path(filename).write_text(
                            json.dumps(value, indent=2) + "\n", encoding="utf-8"
                        )
                        record = review.node(Path(filename))
                after[actual] = record
                files[actual] = Path(filename)
        changes = []
        for filename in sorted(before.keys() | after.keys()):
            old = before.get(filename, {"kind": "missing"})
            new = after.get(filename, {"kind": "missing"})
            if old == new:
                continue
            path_obj = Path(filename)
            owner = next(
                (
                    t
                    for t, _, p in destinations
                    if path_obj == p or p in path_obj.parents
                ),
                tools[0],
            )
            shared = (
                filename == str(paths.CLAUDE_HOME / "CLAUDE.md") and "codex" in tools
            )
            if new["kind"] == "missing":
                operation = "delete"
            elif old["kind"] == "missing":
                operation = "create"
            else:
                operation = "modify"

            is_skills = (
                any(part == "skills" for part in path_obj.parts)
                or path_obj.name.startswith(".ai-config-skills")
            )
            changes.append(
                {
                    "category": "skills" if is_skills else "settings",
                    "tool": owner,
                    "operation": operation,
                    "source": None,
                    "destination": filename,
                    "physical_target": filename,
                    "shared": shared,
                    "reason": "此檔案亦由 Claude 使用" if shared else "套用投影差異",
                }
            )
        result = ApplyPlan(
            temporary,
            tools,
            category,
            before,
            after,
            files,
            changes,
            relevant,
            identity,
            [],
        )
        if result.current_identity() != identity:
            raise StalePreview("目的地在預覽時已有變動，請重試。")
        return result
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def worker_main(filename: str) -> int:
    manifest = Path(filename).resolve(strict=True)
    base = manifest.parent
    value = json.loads(manifest.read_text(encoding="utf-8"))
    home = Path(value["home"])
    if (
        not base.name.startswith("acg-apply-review-")
        or home != base / "home"
        or home != paths.HOME
        or paths.SCRIPT_DIR != base / "data"
    ):
        raise ValueError("Invalid preview worker home")
    if value["category"] not in ("settings", "skills", "all"):
        raise ValueError("Invalid category")
    from .commands.apply import _TOOLS

    if value["dedicated_codex_agents"]:
        marker = paths.SCRIPT_DIR / "codex" / "AGENTS.md"
        marker.parent.mkdir(parents=True)
        marker.touch()
    for tool in value["tools"]:
        if tool not in _TOOLS:
            raise ValueError("Invalid tool")
        stage = Path(value["stages"][tool])
        if stage != base / "stages" / tool or is_reparse_point(stage):
            raise ValueError("Invalid worker stage")
        destination = paths.tool_home(tool)
        destination.mkdir(parents=True, exist_ok=True)
        _TOOLS[tool].apply_internal(stage, destination, category=value["category"])
    return 0


def _put(path: Path, record: dict, content: Path | None) -> None:
    _plain_parents(path)
    current = review.node(path)
    kind = record["kind"]
    if current["kind"] != "missing" and not (
        current["kind"] == kind == "file"
    ):
        if current["kind"] in ("directory", "junction"):
            path.rmdir()
        else:
            path.unlink()
    if kind == "missing":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "directory":
        path.mkdir()
    elif kind in ("symlink", "junction"):
        _link(path, record)
    else:
        temporary = path.with_name(f".{path.name}.acg-{uuid.uuid4().hex}")
        replacement_before = review.node(path)
        try:
            shutil.copy2(content, temporary)
            if review.node(path) != replacement_before:
                raise StalePreview(f"檔案在寫入時已有變動：{path}")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def execute(plan: ApplyPlan) -> Path | None:
    """Caller holds apply_lock; each leaf is checked again before mutation."""
    if plan.current_identity() != plan.identity:
        raise StalePreview("內容在預覽後已有變動，請重新預覽。")
    if not plan.changes:
        return None
    _plain_parents(paths.BACKUP_BASE / "entry")
    backup = paths.BACKUP_BASE / f"apply-review-{uuid.uuid4().hex}"
    backup.mkdir(parents=True)
    changed = [change["destination"] for change in plan.changes]
    saved = {}
    for index, name in enumerate(changed):
        original = plan.before.get(name, {"kind": "missing"})
        if original["kind"] == "file":
            target = backup / str(index)
            shutil.copy2(name, target)
            saved[name] = target
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                name: {
                    "before": plan.before.get(name, {"kind": "missing"}),
                    "content": str(saved[name].name) if name in saved else None,
                }
                for name in changed
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    applied = []
    # Remove descendants before parents; create parents before descendants.
    removals = sorted(
        [
            n
            for n in changed
            if plan.after.get(n, {"kind": "missing"})["kind"] == "missing"
        ],
        key=lambda n: len(Path(n).parts),
        reverse=True,
    )
    writes = sorted(
        [n for n in changed if n not in removals],
        key=lambda n: (
            {"directory": 0, "file": 1, "symlink": 2, "junction": 2}[
                plan.after[n]["kind"]
            ],
            len(Path(n).parts),
        ),
    )
    pending_write = None
    try:
        for name in removals + writes:
            expected = plan.before.get(name, {"kind": "missing"})
            if review.node(Path(name)) != expected:
                raise StalePreview(f"檔案在套用時已有變動：{name}")
            desired = plan.after.get(name, {"kind": "missing"})
            if desired["kind"] != "missing":
                _plain_parents(Path(name))
                missing_parents = []
                for parent in Path(name).parents:
                    if parent.exists():
                        break
                    missing_parents.append(parent)
                # Managed roots omit their container directories. Record those
                # writes too so a failed first apply leaves no empty parents.
                for parent in reversed(missing_parents):
                    parent.mkdir()
                    applied.append((str(parent), {"kind": "directory"}))
            pending_write = (name, expected, desired)
            _put(Path(name), desired, plan.files.get(name))
            applied.append((name, desired))
            pending_write = None
    except (OSError, RuntimeError, ValueError) as exc:
        failures = []
        if pending_write is not None:
            name, expected, desired = pending_write
            try:
                current = review.node(Path(name))
                if current != expected:
                    # A failed type/link replacement may already have removed
                    # the original. Restore only a recognized intermediate
                    # state, and recheck it below before touching the path.
                    if current == {"kind": "missing"} or current == desired:
                        applied.append((name, current))
                    else:
                        raise RuntimeError("外部修改，保留目前內容")
            except (OSError, RuntimeError, ValueError) as restore:
                failures.append(f"{name}: {restore}")
        for name, written in reversed(applied):
            try:
                if review.node(Path(name)) != written:
                    raise RuntimeError("外部修改，保留目前內容")
                _put(
                    Path(name),
                    plan.before.get(name, {"kind": "missing"}),
                    saved.get(name),
                )
            except (OSError, RuntimeError) as restore:
                failures.append(f"{name}: {restore}")
        raise ApplyFailure(
            str(exc) + ("; " + "; ".join(failures) if failures else ""),
            backup,
            bool(failures),
        ) from exc
    return backup
