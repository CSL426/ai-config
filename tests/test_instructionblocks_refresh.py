"""apply keeps whether a machine has the memory block, not what the block said."""

from ai_config import memory
from ai_config.instructionblocks import preserve_memory_block

OLD = (
    f"{memory.BLOCK_BEGIN}\n## 共用記憶\n- 認領被拒表示別的 session 正持有它。\n"
    f"{memory.BLOCK_END}"
)


def test_a_stale_live_block_is_replaced_by_the_current_rules() -> None:
    """Preserving the live block kept its words too, so no rule change ever landed.

    The handoff rules were rewritten and gathered into the database; every
    apply afterwards put the machine's old copy back, and three machines
    ran on rules the code no longer matched.
    """
    source = b"# Global\n\nrules\n"
    live = f"# Global\n\nold rules\n\n{OLD}\n".encode()

    merged = preserve_memory_block(source, live).decode()

    assert memory.RULES_BLOCK.strip() in merged
    assert "正持有它" not in merged
    assert merged.startswith("# Global\n\nrules\n")


def test_a_machine_without_the_block_still_gets_none() -> None:
    """Whether memory is on stays each machine's own choice."""
    source = f"# Global\n\n{memory.RULES_BLOCK}".encode()

    merged = preserve_memory_block(source, b"# Global\n").decode()

    assert memory.BLOCK_BEGIN not in merged
