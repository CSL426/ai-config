"""Install local skills and explicitly remove their sources and mirrors."""

import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from ..console import confirm as confirm_prompt
from ..console import log_error, log_info, log_success
from ..paths import (
    ACKNOWLEDGED_NAME,
    AGY_CANONICAL_SKILLS,
    AGY_HOME,
    AGY_LEGACY_SKILLS,
    CLAUDE_HOME,
    CODEX_CANONICAL_SKILLS,
    CODEX_LEGACY_SKILLS,
    ENTRYPOINT,
    EXCLUDED_FILES,
    MANIFEST_NAME,
    SCRIPT_DIR,
)
from ..safety import is_reparse_point


def _validate_name(name: str) -> None:
    if (
        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name)
        or name.upper() in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"(?:COM|LPT)[0-9]", name.upper())
    ):
        raise ValueError(f"Unsafe skill name: {name!r}")


def _plain_path(path: Path) -> None:
    for part in (path, *path.parents):
        if is_reparse_point(part):
            raise ValueError(f"Refusing reparse point: {part.as_posix()}")
        if part != path and part.exists() and not part.is_dir():
            raise ValueError(f"Parent is not a directory: {part.as_posix()}")


def _plain_tree(path: Path) -> None:
    _plain_path(path)
    if not path.is_dir():
        raise ValueError(f"Not a skill directory: {path.as_posix()}")
    # Walk without following links, including Junctions on Python 3.11.
    for child in path.iterdir():
        if is_reparse_point(child):
            raise ValueError(f"Refusing reparse point: {child.as_posix()}")
        mode = child.lstat().st_mode
        if stat.S_ISDIR(mode):
            _plain_tree(child)
        elif not stat.S_ISREG(mode):
            raise ValueError(f"Not a regular skill file: {child.as_posix()}")


def _scalar(value: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        result, end = json.JSONDecoder().raw_decode(value)
        trailing = value[end:].strip()
        if trailing and not trailing.startswith("#"):
            raise ValueError("Invalid quoted frontmatter field")
        return result
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", value)
        if not match:
            raise ValueError("Invalid quoted frontmatter field")
        return match.group(1).replace("''", "'")
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if (
        not value
        or value[0] in "[{&*!#>|"
        or value.lower() in {"null", "~", "true", "false"}
    ):
        raise ValueError("Expected a non-empty text frontmatter field")
    return value


def _skill_name(source: Path) -> str:
    lines = (source / "SKILL.md").read_text(
        encoding="utf-8-sig"
    ).splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("SKILL.md requires YAML frontmatter")
    header = lines[1:lines.index("---", 1)]
    fields = {}
    for index, line in enumerate(header):
        match = re.match(r"^(name|description):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if key in fields:
            raise ValueError(f"Duplicate frontmatter field: {key}")
        if key == "description" and re.fullmatch(
            r"[>|][-+]?\s*(?:#.*)?", value
        ):
            block = []
            for following in header[index + 1:]:
                if following and not following[0].isspace():
                    break
                block.append(following.strip())
            fields[key] = " ".join(block).strip()
        else:
            fields[key] = _scalar(value)
    if not fields.get("name") or not fields.get("description"):
        raise ValueError("SKILL.md requires name and description fields")
    name = fields["name"]
    _validate_name(name)
    return name


def _copy_ignore(directory: str, names: list[str]) -> list[str]:
    return [
        name for name in names
        if name in EXCLUDED_FILES or name in {".git", ".ai-config"}
        or name.startswith(".ai-config-")
    ]


def _add(source_text: str) -> int:
    source = Path(os.path.abspath(Path(source_text).expanduser()))
    _plain_tree(source)
    name = _skill_name(source)
    destinations = [
        SCRIPT_DIR / "claude" / "skills" / name,
        CLAUDE_HOME / "skills" / name,
    ]
    for destination in destinations:
        _plain_path(destination)
        if destination.exists():
            raise ValueError(
                "Skill already exists; refusing to overwrite: "
                f"{destination.as_posix()}"
            )
        if destination.is_relative_to(source):
            raise ValueError("Install source cannot contain a destination")
    created = []
    # Snapshot before writing either copy, so a read failure leaves no install.
    with tempfile.TemporaryDirectory(prefix="acg-skill-") as temporary:
        staged = Path(temporary) / name
        shutil.copytree(source, staged, ignore=_copy_ignore)
        try:
            for destination in destinations:
                _plain_path(destination)
                destination.mkdir(parents=True, exist_ok=False)
                created.append(destination)
                shutil.copytree(staged, destination, dirs_exist_ok=True)
        except (OSError, RuntimeError, ValueError):
            for destination in reversed(created):
                _plain_tree(destination)
                shutil.rmtree(destination)
            raise
    for destination in destinations:
        log_success(f"Installed {name}: {destination.as_posix()}")
    log_info(
        f"Run {ENTRYPOINT} apply --category skills to deploy to Codex/agy. "
        "Shared copies with the same name take precedence."
    )
    return 0


def _skill_roots() -> list[Path]:
    roots = [
        SCRIPT_DIR / tool / "skills" for tool in ("claude", "codex", "agy")
    ] + [
        SCRIPT_DIR / "claude" / "shared" / target
        for target in ("both", "codex", "agy")
    ] + [
        CLAUDE_HOME / "skills",
        CODEX_CANONICAL_SKILLS,
        AGY_CANONICAL_SKILLS,
    ]
    aliases = {
        CODEX_LEGACY_SKILLS: (CODEX_CANONICAL_SKILLS,),
        AGY_LEGACY_SKILLS: (AGY_CANONICAL_SKILLS,),
        AGY_HOME / "skills": (AGY_CANONICAL_SKILLS, AGY_LEGACY_SKILLS),
    }
    for alias, expected in aliases.items():
        _plain_path(alias.parent)
        if is_reparse_point(alias):
            target = alias.resolve(strict=True)
            if target not in expected:
                raise ValueError(
                    f"Unexpected skills link target: {alias.as_posix()}"
                )
            roots.append(target)
        else:
            roots.append(alias)
    return list(dict.fromkeys(roots))


def _remove_plan(name: str) -> tuple[list[Path], dict[Path, str]]:
    for root in (SCRIPT_DIR / "claude", CLAUDE_HOME):
        agent = root / "agents" / f"{name}.md"
        _plain_path(agent)
        if agent.exists():
            raise ValueError(
                f"Agent would regenerate this skill: {agent.as_posix()}. "
                "Remove or rename the agent separately first."
            )
    copies = []
    markers = {}
    for root in _skill_roots():
        candidate = root / name
        _plain_path(candidate)
        if candidate.exists():
            _plain_tree(candidate)
            copies.append(candidate)
        for marker_name in (MANIFEST_NAME, ACKNOWLEDGED_NAME):
            marker = root / marker_name
            _plain_path(marker)
            if marker.exists():
                if not marker.is_file():
                    raise ValueError(f"Invalid skill marker: {marker}")
                lines = marker.read_text(encoding="utf-8").splitlines(True)
                kept = [
                    line for line in lines
                    if os.path.normcase(line.strip()) != os.path.normcase(name)
                ]
                if kept != lines:
                    markers[marker] = "".join(kept)
    return copies, markers


def _remove(name: str) -> int:
    _validate_name(name)
    copies, markers = _remove_plan(name)
    if not copies and not markers:
        log_info(f"Skill not found; nothing to remove: {name}")
        return 0
    log_info(f"Permanently remove skill {name} from these paths:")
    for path in copies:
        print(f"  {path.as_posix()}")
    for path in markers:
        print(f"  {path.as_posix()} (remove name from index)")
    if not confirm_prompt("Remove these skill copies permanently? [y/N] "):
        log_info("Cancelled; no skill copies removed.")
        return 1
    # A prompt can stay open while another process changes the installation.
    if _remove_plan(name) != (copies, markers):
        raise ValueError("Skill paths changed during confirmation; run again")
    for path in copies:
        shutil.rmtree(path)
        log_success(f"Removed: {path.as_posix()}")
    for path, content in markers.items():
        path.write_text(content, encoding="utf-8", newline="")
    log_info("Plugin installations and project-local skills are left in place.")
    return 0


def run_skill(args: list[str]) -> int:
    if len(args) != 2 or args[0] not in ("add", "remove"):
        log_error(
            f"Usage: {ENTRYPOINT} skill add <local-directory> | "
            "skill remove <name> | skill guide"
        )
        return 1
    try:
        return _add(args[1]) if args[0] == "add" else _remove(args[1])
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
