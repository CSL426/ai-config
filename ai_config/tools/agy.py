"""Antigravity CLI (~/.gemini/antigravity-cli/) projection and sync."""

import json
import os
import shutil
from pathlib import Path

from ..console import log_header, log_info, log_success, log_warn
from ..fsops import copy_file_to_stage, first_existing_file, mirror_dir
from ..links import ensure_agy_shared_links
from ..localsettings import (
    filter_settings,
    merge_settings,
    read_settings,
    shared_settings,
    write_settings,
)
from ..paths import (
    AGY_CANONICAL_SKILLS,
    AGY_HOME,
    CLAUDE_HOME,
    SCRIPT_DIR,
    WINDOWS_MODE,
    claude_source_dir,
)
from ..safety import assert_managed_paths_safe
from ..skills import (
    apply_managed_skills,
    project_agents_to_skills,
    reconcile_managed_skills,
    sync_shared_skills,
    sync_skills,
)

_MACHINE_LOCAL_SETTINGS = frozenset({"trustedWorkspaces", "permissions"})
_SETTINGS_LABEL = "Antigravity settings.json"


def filter_agy_settings(text: str) -> str:
    return filter_settings(text, _MACHINE_LOCAL_SETTINGS, _SETTINGS_LABEL)


def merge_agy_settings(source_text: str, target_text: str) -> str:
    return merge_settings(
        source_text, target_text, _MACHINE_LOCAL_SETTINGS, _SETTINGS_LABEL
    )


def shared_agy_settings(text: str) -> dict[str, object]:
    return shared_settings(text, _MACHINE_LOCAL_SETTINGS, _SETTINGS_LABEL)


_read_settings = read_settings
_write_settings = write_settings


def _replace_json_path(value: object, source: str, destination: str) -> object:
    if isinstance(value, str):
        return value.replace(source, destination)
    if isinstance(value, list):
        return [_replace_json_path(item, source, destination) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_json_path(item, source, destination)
            for key, item in value.items()
        }
    return value


def stage_projection(dst: Path) -> None:
    src = SCRIPT_DIR / "agy"
    claude_src = claude_source_dir()
    dst.mkdir(parents=True, exist_ok=True)

    mcp_source = first_existing_file(src / "mcp_config.json", claude_src / "mcp.json")
    if mcp_source is not None:
        copy_file_to_stage(mcp_source, dst / "mcp_config.json")

    settings = src / "settings.json"
    if settings.is_file():
        _write_settings(
            settings,
            dst / "settings.json",
            filter_agy_settings(_read_settings(settings)),
        )

    project_agents_to_skills(claude_src / "agents", dst / "skills")
    if (src / "skills").is_dir():
        sync_skills(src / "skills", dst / "skills")
    if (claude_src / "skills").is_dir():
        sync_skills(claude_src / "skills", dst / "skills")
    sync_shared_skills("agy", dst / "skills")

    if (claude_src / "plugins").is_dir():
        mirror_dir(
            claude_src / "plugins",
            dst / "plugins",
            allow_internal_symlinks=True,
        )
        registry = dst / "plugins" / "installed_plugins.json"
        if registry.is_file():
            registry_stat = registry.stat()
            document = json.loads(registry.read_text(encoding="utf-8"))
            document = _replace_json_path(
                document,
                str(CLAUDE_HOME / "plugins"),
                str(AGY_HOME / "plugins"),
            )
            registry.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.utime(
                registry,
                ns=(registry_stat.st_atime_ns, registry_stat.st_mtime_ns),
            )


def preflight_init() -> bool:
    src = AGY_HOME
    dst = SCRIPT_DIR / "agy"

    if not src.is_dir():
        return True

    assert_managed_paths_safe(src, ("settings.json",), ())
    assert_managed_paths_safe(dst, ("settings.json",), ())
    return True


def init() -> bool:
    log_header("Init Antigravity CLI")
    src = AGY_HOME
    dst = SCRIPT_DIR / "agy"
    if not src.is_dir():
        log_warn(f"Antigravity CLI directory not found: {src}")
        return True
    if not preflight_init():
        return False

    if (src / "settings.json").is_file():
        settings = src / "settings.json"
        _write_settings(
            settings,
            dst / "settings.json",
            filter_agy_settings(_read_settings(settings)),
        )
        log_success("settings.json (filtered, machine-local settings excluded)")

    log_info("All other files are projected from claude/ during apply — nothing else to init")
    log_success("Antigravity CLI init complete")
    return True


def apply_internal(src: Path, dst: Path) -> None:
    if (src / "mcp_config.json").is_file():
        shutil.copy2(src / "mcp_config.json", dst / "mcp_config.json")
        log_success("mcp_config.json")

    if (src / "settings.json").is_file():
        source = src / "settings.json"
        destination = dst / "settings.json"
        if destination.is_file():
            merged = merge_agy_settings(
                _read_settings(source),
                _read_settings(destination),
            )
            _write_settings(source, destination, merged)
            log_success("settings.json (merged, preserved machine-local settings)")
        else:
            _write_settings(
                source,
                destination,
                filter_agy_settings(_read_settings(source)),
            )
            log_success("settings.json (fresh copy, machine-local settings excluded)")

    skill_destination = AGY_CANONICAL_SKILLS if WINDOWS_MODE else dst / "skills"
    if (src / "skills").is_dir():
        if WINDOWS_MODE:
            skill_destination.mkdir(parents=True, exist_ok=True)
        else:
            ensure_agy_shared_links()
        apply_managed_skills(src / "skills", skill_destination)
        log_success("skills/")
    reconcile_managed_skills(src / "skills", skill_destination)
    if WINDOWS_MODE and skill_destination.is_dir():
        ensure_agy_shared_links()

    if (src / "plugins").is_dir():
        mirror_dir(
            src / "plugins",
            dst / "plugins",
            allow_internal_symlinks=True,
        )
        log_success("plugins/")
