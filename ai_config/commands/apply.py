"""apply / init commands: deploy repo config to tool homes, or gather it back."""

from ..backup import create_backup
from ..categories import validate_category
from ..console import log_error, log_warn
from ..instructionblocks import prepare_instruction_blocks
from ..links import preflight_windows_links
from ..locking import apply_lock
from ..paths import ALL_TOOLS, tool_home
from ..safety import assert_tool_destinations_safe
from ..staging import staged_projections
from ..tools import agy, claude, codex

_TOOLS = {"claude": claude, "codex": codex, "agy": agy}
_HEADERS = {"claude": "Claude", "codex": "Codex", "agy": "Antigravity CLI"}


def apply_tools(tools: list[str], *, category: str = "all") -> bool:
    validate_category(category)
    if not tools or any(tool not in ALL_TOOLS for tool in tools):
        raise ValueError("Invalid apply tool scope")
    snapshot = None
    try:
        with staged_projections(tools, _TOOLS, _HEADERS, category=category) as stages:
            assert_tool_destinations_safe(tools, stages, category=category)
            prepare_instruction_blocks(stages, category=category)
            preflight_windows_links(tools, category=category)
            with apply_lock():
                snapshot = create_backup(tools, stages, category=category)
                for tool in tools:
                    home_dir = tool_home(tool)
                    home_dir.mkdir(parents=True, exist_ok=True)
                    _TOOLS[tool].apply_internal(stages[tool], home_dir, category=category)
    except Exception as exc:  # noqa: BLE001 - top-level guard must not crash
        log_error(f"Failed to apply config: {exc}")
        if snapshot is not None:
            log_warn(
                "Live config may be partially updated. "
                f"Restore from backup if needed: {snapshot}"
            )
        return False
    return True


def apply_tool(tool: str, *, category: str = "all") -> bool:
    return apply_tools([tool], category=category)

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
