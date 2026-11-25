"""A hook another tool installs per machine stays on that machine."""

from ai_config import hooks

HERDR = {
    "matcher": "^(startup|resume|clear|compact|fork)$",
    "hooks": [{
        "type": "command",
        "command": "bash '/home/someone/.claude/hooks/herdr-agent-state.sh' session",
        "timeout": 10,
    }],
}
SHARED = {"hooks": [{"type": "command", "command": "echo shared", "timeout": 5}]}


def test_gather_leaves_the_herdr_hook_out() -> None:
    live = {"theme": "dark", "hooks": {"SessionStart": [HERDR, SHARED]}}

    assert hooks.without_hooks(live) == {"theme": "dark", "hooks": {"SessionStart": [SHARED]}}


def test_apply_keeps_the_herdr_hook_on_this_machine() -> None:
    database = {"theme": "light", "hooks": {"SessionStart": [SHARED]}}
    live = {"theme": "dark", "hooks": {"SessionStart": [HERDR, SHARED]}}

    merged = hooks.preserve_hooks(database, live)

    assert merged["theme"] == "light"
    assert HERDR in merged["hooks"]["SessionStart"]
    assert SHARED in merged["hooks"]["SessionStart"]


def test_a_machine_without_herdr_gets_nothing_new() -> None:
    database = {"hooks": {"SessionStart": [SHARED]}}

    assert hooks.preserve_hooks(database, {}) == database
