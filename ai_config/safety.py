"""Path preflight checks for managed repository and live destinations."""

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from .paths import (
    AGY_CANONICAL_SKILLS,
    AGY_HOME,
    AGY_LEGACY_SKILLS,
    CLAUDE_HOME,
    CODEX_CANONICAL_SKILLS,
    CODEX_HOME,
    CODEX_LEGACY_SKILLS,
    WINDOWS_MODE,
)


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        attributes = 0
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )


def _is_within_existing_root(path: Path, root: Path) -> bool:
    return any(_same_path(candidate, root) for candidate in (path, *path.parents))


def assert_root_not_reparse(path: Path, label: str = "managed root") -> None:
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point {label}: {path}")


def assert_no_symlinks(path: Path) -> None:
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point in managed path: {path}")
    if not path.exists() or not path.is_dir():
        return
    for child in path.rglob("*"):
        if is_reparse_point(child):
            raise RuntimeError(f"Refusing reparse point in managed path: {child}")


def assert_internal_symlinks(path: Path) -> None:
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point managed root: {path}")
    if not path.exists() or not path.is_dir():
        return
    root = Path(os.path.abspath(path))
    for child in path.rglob("*"):
        if not is_reparse_point(child):
            continue
        if not child.is_symlink():
            raise RuntimeError(f"Refusing non-symlink reparse point: {child}")
        try:
            raw_target = Path(os.readlink(child))
        except OSError as exc:
            raise RuntimeError(f"Cannot read managed symlink target: {child}") from exc
        if raw_target.is_absolute():
            raise RuntimeError(f"Refusing absolute managed symlink: {child}")
        try:
            resolved = (child.parent / raw_target).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"Refusing broken managed symlink: {child}") from exc
        if not _is_within_existing_root(resolved, root):
            raise RuntimeError(f"Refusing managed symlink escaping root: {child}")


def assert_safe_write_target(path: Path) -> None:
    if is_reparse_point(path):
        raise RuntimeError(f"Refusing reparse point file destination: {path}")
    parent = path.parent
    if is_reparse_point(parent):
        raise RuntimeError(f"Refusing reparse point parent destination: {parent}")


def codex_agents_shared_target(path: "Path | None" = None) -> "Path | None":
    agents = path or CODEX_HOME / "AGENTS.md"
    if not is_reparse_point(agents):
        return None
    if not agents.is_symlink():
        raise RuntimeError(f"Refusing non-symlink Codex AGENTS reparse point: {agents}")
    try:
        target = Path(os.readlink(agents))
    except OSError as exc:
        raise RuntimeError(f"Cannot read Codex AGENTS link target: {agents}") from exc
    if not target.is_absolute():
        target = agents.parent / target
    target = Path(os.path.abspath(target))
    expected = Path(os.path.abspath(CLAUDE_HOME / "CLAUDE.md"))
    if not _same_path(target, expected):
        raise RuntimeError(f"Refusing Codex AGENTS link target mismatch: {agents}")
    assert_safe_write_target(expected)
    if not expected.is_file():
        raise RuntimeError(f"Refusing broken Codex AGENTS shared target: {expected}")
    return expected


def assert_managed_paths_safe(
    root: Path, file_names: tuple[str, ...], directory_names: tuple[str, ...]
) -> None:
    _assert_tool_root(root)
    for name in file_names:
        assert_safe_write_target(root / name)
    for name in directory_names:
        assert_no_symlinks(root / name)


def _assert_tool_root(path: Path) -> None:
    assert_root_not_reparse(path, "tool home")


def _assert_expected_agy_link() -> None:
    skills = AGY_HOME / "skills"
    if not is_reparse_point(skills):
        assert_no_symlinks(skills)
        return
    target = Path(os.readlink(skills))
    if not target.is_absolute():
        target = skills.parent / target
    if not any(
        _same_path(target.resolve(), expected.resolve())
        for expected in (AGY_CANONICAL_SKILLS, AGY_LEGACY_SKILLS)
    ):
        raise RuntimeError(
            f"Refusing reparse point Antigravity skills target mismatch: {skills}"
        )


def _assert_expected_legacy_path(
    legacy: Path,
    canonical: Path,
    tool_label: str,
) -> None:
    if not is_reparse_point(legacy):
        assert_no_symlinks(legacy)
        return
    try:
        target = Path(os.readlink(legacy))
    except OSError as exc:
        raise RuntimeError(
            f"Cannot read legacy {tool_label} skills target: {legacy}"
        ) from exc
    if not target.is_absolute():
        target = legacy.parent / target
    if not _same_path(target.resolve(), canonical.resolve()):
        raise RuntimeError(
            f"Refusing reparse point legacy {tool_label} skills target mismatch: "
            f"{legacy}"
        )


def assert_tool_destinations_safe(
    tools: list[str],
    stages: "Mapping[str, Path] | None" = None,
) -> None:
    for tool in tools:
        if tool == "claude":
            assert_managed_paths_safe(
                CLAUDE_HOME,
                ("CLAUDE.md", "mcp.json", "settings.json", "statusline.sh"),
                ("rules", "agents", "commands"),
            )
        elif tool == "codex":
            assert_managed_paths_safe(
                CODEX_HOME,
                ("config.toml",),
                ("rules",),
            )
            assert_no_symlinks(CODEX_CANONICAL_SKILLS)
            _assert_expected_legacy_path(
                CODEX_LEGACY_SKILLS,
                CODEX_CANONICAL_SKILLS,
                "Codex",
            )
            codex_agents_shared_target()
        elif tool == "agy":
            assert_root_not_reparse(AGY_HOME, "Antigravity CLI root")
            for name in ("mcp_config.json", "settings.json"):
                assert_safe_write_target(AGY_HOME / name)
            if stages is None or (stages[tool] / "plugins").is_dir():
                assert_internal_symlinks(AGY_HOME / "plugins")
            assert_no_symlinks(AGY_CANONICAL_SKILLS)
            _assert_expected_legacy_path(
                AGY_LEGACY_SKILLS,
                AGY_CANONICAL_SKILLS,
                "Antigravity",
            )
            if not WINDOWS_MODE:
                _assert_expected_agy_link()
