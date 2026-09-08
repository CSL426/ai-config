"""Keep memory enablement local when applying shared instruction files."""

from collections.abc import Mapping
from pathlib import Path

from .categories import includes
from .paths import tool_home
from .safety import codex_agents_shared_target

_BEGIN = b"<!-- acg:memory:begin -->"
_END = b"<!-- acg:memory:end -->"


def _block_span(content: bytes) -> tuple[int, int] | None:
    starts, ends = content.count(_BEGIN), content.count(_END)
    if starts == ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise ValueError("Malformed or duplicate acg memory management markers")
    begin, end = content.index(_BEGIN), content.index(_END)
    if begin >= end:
        raise ValueError("Malformed acg memory management marker order")
    return begin, end + len(_END)


def preserve_memory_block(source: bytes, live: bytes) -> bytes:
    """Use source rules and exactly the live managed block, if any."""
    source_span, live_span = _block_span(source), _block_span(live)
    block = live[live_span[0]:live_span[1]] if live_span else b""
    if source_span:
        return source[:source_span[0]] + block + source[source_span[1]:]
    if not block:
        return source
    separator = b"" if not source or source.endswith(b"\n") else b"\n"
    return source + separator + block + b"\n"


def prepare_instruction_blocks(
    stages: Mapping[str, Path], *, category: str = "all"
) -> None:
    """Called after destination preflight, before any backup or live mutation."""
    if not includes(category, "settings"):
        return
    for tool, name in (("claude", "CLAUDE.md"), ("codex", "AGENTS.md")):
        if tool not in stages:
            continue
        staged = stages[tool] / name
        live = tool_home(tool) / name
        if tool == "codex":
            live = codex_agents_shared_target(live) or live
        live_bytes = live.read_bytes() if live.is_file() else b""
        _block_span(live_bytes)
        if staged.is_file():
            source = staged.read_bytes()
            merged = preserve_memory_block(source, live_bytes)
            if merged != source:
                staged.write_bytes(merged)
