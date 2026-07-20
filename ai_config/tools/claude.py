"""Claude Code: staging projection, init, apply.
Claude is the source of truth — init syncs everything, including deletions."""

import shutil
from pathlib import Path

from ..console import log_error, log_header, log_info, log_success
from ..fsops import copy_file_to_stage, mirror_dir, overlay_dir_to_stage, safe_cp
from ..localsettings import (
    filter_settings,
    merge_settings,
    read_settings,
    shared_settings,
    write_settings,
)
from ..paths import (
    CLAUDE_HOME,
    CLAUDE_MANAGED_DIRS,
    CLAUDE_MANAGED_FILES,
    SCRIPT_DIR,
    claude_source_dir,
)
from ..safety import assert_managed_paths_safe

_MACHINE_LOCAL_SETTINGS = frozenset({"permissions"})
_SETTINGS_LABEL = "Claude settings.json"


def filter_claude_settings(text: str) -> str:
    return filter_settings(text, _MACHINE_LOCAL_SETTINGS, _SETTINGS_LABEL)


def merge_claude_settings(source_text: str, target_text: str) -> str:
    return merge_settings(
        source_text, target_text, _MACHINE_LOCAL_SETTINGS, _SETTINGS_LABEL
    )


def shared_claude_settings(text: str) -> dict[str, object]:
    return shared_settings(text, _MACHINE_LOCAL_SETTINGS, _SETTINGS_LABEL)


def _stage_filtered_settings(source: Path, destination: Path) -> None:
    write_settings(
        source, destination, filter_claude_settings(read_settings(source))
    )


def stage_projection(dst: Path) -> None:
    src = claude_source_dir()
    dst.mkdir(parents=True, exist_ok=True)
    for name in CLAUDE_MANAGED_FILES:
        if name == "settings.json" and (src / name).is_file():
            _stage_filtered_settings(src / name, dst / name)
        else:
            copy_file_to_stage(src / name, dst / name)
    for name in CLAUDE_MANAGED_DIRS:
        overlay_dir_to_stage(src / name, dst / name)


def preflight_init() -> bool:
    src = CLAUDE_HOME
    dst = SCRIPT_DIR / "claude"

    if not src.is_dir():
        log_error(f"Claude config directory not found: {src}")
        return False

    assert_managed_paths_safe(
        src, tuple(CLAUDE_MANAGED_FILES), tuple(CLAUDE_MANAGED_DIRS)
    )
    assert_managed_paths_safe(
        dst, tuple(CLAUDE_MANAGED_FILES), tuple(CLAUDE_MANAGED_DIRS)
    )
    return True


def init() -> bool:
    log_header("Init Claude")
    if not preflight_init():
        return False
    src = CLAUDE_HOME
    dst = SCRIPT_DIR / "claude"

    for name in CLAUDE_MANAGED_FILES:
        if (src / name).is_file():
            if name == "settings.json":
                _stage_filtered_settings(src / name, dst / name)
                log_success(
                    "settings.json (filtered, machine-local settings excluded)"
                )
            else:
                safe_cp(src / name, dst / name)
                log_success(name)
        elif (dst / name).is_file():
            (dst / name).unlink()
            log_info(f"{name} removed (no longer in {src})")

    for name in CLAUDE_MANAGED_DIRS:
        if (src / name).is_dir():
            mirror_dir(src / name, dst / name)
            log_success(f"{name}/")
        elif (dst / name).is_dir():
            shutil.rmtree(dst / name)
            log_info(f"{name}/ removed (no longer in {src})")

    log_success("Claude init complete")
    return True


def apply_internal(src: Path, dst: Path) -> None:
    for name in CLAUDE_MANAGED_FILES:
        if not (src / name).is_file():
            continue
        if name == "settings.json":
            source = src / name
            destination = dst / name
            if destination.is_file():
                merged = merge_claude_settings(
                    read_settings(source),
                    read_settings(destination),
                )
                write_settings(source, destination, merged)
                log_success(
                    "settings.json (merged, preserved machine-local settings)"
                )
            else:
                write_settings(
                    source,
                    destination,
                    filter_claude_settings(read_settings(source)),
                )
                log_success(
                    "settings.json (fresh copy, machine-local settings excluded)"
                )
        else:
            safe_cp(src / name, dst / name)
            log_success(name)

    for name in CLAUDE_MANAGED_DIRS:
        if (src / name).is_dir():
            mirror_dir(src / name, dst / name)
            log_success(f"{name}/")
