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


def test_gather_keeps_the_database_wording_over_a_stale_machine() -> None:
    """A machine still on the old release pushed its old rules back.

    That happened: the database carried the new handoff rules, A4000 had
    not applied them yet, and its `acg push` gathered the old block and
    reverted the database for every machine.
    """
    from ai_config.instructionblocks import gather_memory_block

    begin, end = "<!-- acg:memory:begin -->", "<!-- acg:memory:end -->"
    live = f"# 我的規則\n改過的一行\n{begin}\n舊的交接規則\n{end}\n".encode()
    stored = f"# 我的規則\n{begin}\n新的交接規則\n{end}\n".encode()

    gathered = gather_memory_block(live, stored).decode()

    assert "改過的一行" in gathered, "區塊以外的內容仍然以這台為準"
    assert "新的交接規則" in gathered and "舊的交接規則" not in gathered


def test_gather_leaves_a_machine_without_the_block_alone() -> None:
    from ai_config.instructionblocks import gather_memory_block

    stored = b"<!-- acg:memory:begin -->\nx\n<!-- acg:memory:end -->\n"
    assert gather_memory_block(b"# only mine\n", stored) == b"# only mine\n"


def test_gather_with_no_stored_block_uses_this_release() -> None:
    from ai_config.instructionblocks import gather_memory_block
    from ai_config.memory import RULES_BLOCK

    live = b"<!-- acg:memory:begin -->\nold\n<!-- acg:memory:end -->\n"
    assert gather_memory_block(live, b"") == RULES_BLOCK.strip().encode() + b"\n"
