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
    """Use source rules, with the memory block only where the machine has one.

    Whether memory is enabled is this machine's choice, so presence comes
    from live. The words do not: keeping live's copy verbatim meant no
    change to the rules ever reached a machine that already had them, so
    the block is always the current one.
    """
    from .memory import RULES_BLOCK

    source_span, live_span = _block_span(source), _block_span(live)
    block = RULES_BLOCK.strip().encode("utf-8") if live_span else b""
    if live_span and b"\r\n" in live[live_span[0]:live_span[1]]:
        # Windows 上的 live 檔是 CRLF;區塊跟著用,檔案才不會混兩種換行
        block = block.replace(b"\n", b"\r\n")
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
