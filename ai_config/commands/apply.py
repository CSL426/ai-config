"""apply / init commands: deploy repo config to tool homes, or gather it back."""

from ..backup import create_backup
from ..console import log_error, log_warn
from ..links import preflight_windows_links
from ..locking import apply_lock
from ..paths import ALL_TOOLS, tool_home
from ..safety import assert_tool_destinations_safe
from ..staging import staged_projections
from ..tools import agy, claude, codex

_TOOLS = {"claude": claude, "codex": codex, "agy": agy}
_HEADERS = {"claude": "Claude", "codex": "Codex", "agy": "Antigravity CLI"}


def apply_tools(tools: list[str]) -> bool:
    snapshot = None
    try:
        with staged_projections(tools, _TOOLS, _HEADERS) as stages:
            assert_tool_destinations_safe(tools, stages)
            preflight_windows_links(tools)
            with apply_lock():
                snapshot = create_backup(tools, stages)
                for tool in tools:
                    home_dir = tool_home(tool)
                    home_dir.mkdir(parents=True, exist_ok=True)
                    _TOOLS[tool].apply_internal(stages[tool], home_dir)
    except Exception as exc:  # noqa: BLE001 - top-level guard must not crash
        log_error(f"Failed to apply config: {exc}")
        if snapshot is not None:
            log_warn(
                "Live config may be partially updated. "
                f"Restore from backup if needed: {snapshot}"
            )
        return False
    return True


def apply_tool(tool: str) -> bool:
    return apply_tools([tool])

def _selected_tools(tool: str) -> list[str]:
    return [name for name in ALL_TOOLS if tool == "all" or tool == name]


def _init_tools(tool: str) -> bool:
    selected = _selected_tools(tool)
    try:
        if len(selected) > 1:
            for selected_tool in selected:
                if not _TOOLS[selected_tool].preflight_init():
                    return False
        ok = True
        for selected_tool in selected:
            ok = _TOOLS[selected_tool].init() and ok
        return ok
    except Exception as exc:  # noqa: BLE001 - top-level guard must not crash
        log_error(str(exc))
        return False
