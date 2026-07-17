"""Cross-CLI plugin drift detection for status output."""

import json
import re
from pathlib import Path

from .console import CYAN, NC, log_success, log_warn
from .paths import AGY_HOME, CODEX_HOME, SCRIPT_DIR, claude_source_dir

_CODEX_KEY = re.compile(r'^\[plugins\."([^"]*)"\]', re.MULTILINE)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def check_plugin_drift() -> None:
    drift = 0
    claude_settings = claude_source_dir() / "settings.json"
    settings = _read_json(claude_settings) if claude_settings.is_file() else {}
    enabled = settings.get("enabledPlugins", {})
    claude_keys: set[str] = set()
    if isinstance(enabled, dict):
        claude_keys = {
            key
            for key, value in enabled.items()
            if isinstance(key, str) and isinstance(value, bool)
        }

    agy_registry = AGY_HOME / "plugins" / "installed_plugins.json"
    if agy_registry.is_file() and claude_keys:
        registry = _read_json(agy_registry)
        plugins = registry.get("plugins", {})
        agy_keys = plugins.keys() if isinstance(plugins, dict) else ()
        for key in agy_keys:
            if key not in claude_keys:
                log_warn(f"agy has plugin not tracked in claude/settings.json: {key}")
                print(f"    remove with: {CYAN}agy plugin uninstall {key}{NC}")
                drift += 1

    repo_codex = SCRIPT_DIR / "codex" / "config.toml"
    live_codex = CODEX_HOME / "config.toml"
    if repo_codex.is_file() and live_codex.is_file():
        repo_keys = set(_CODEX_KEY.findall(repo_codex.read_text(encoding="utf-8")))
        live_keys = _CODEX_KEY.findall(live_codex.read_text(encoding="utf-8"))
        for key in live_keys:
            if key not in repo_keys:
                log_warn(
                    f"codex live config has plugin not in repo codex/config.toml: {key}"
                )
                print(
                    f"    next {CYAN}apply{NC} removes the config block; "
                    f"also run {CYAN}codex{NC} plugin uninstall if installed"
                )
                drift += 1

    if drift == 0:
        log_success("No plugin drift detected")
