"""Settings keys that must stay per-machine rather than syncing."""

import json
from pathlib import Path

from test_apply_projection import run_ai_config, write
from test_sync_logic import make_repo

from ai_config.tools.claude import (
    filter_claude_settings,
    merge_claude_settings,
)


def test_filter_drops_every_machine_local_key() -> None:
    text = json.dumps(
        {
            "model": "opus",
            "modelSettings": {"opus": {"effortLevel": "high"}},
            "theme": "dark",
            "permissions": {"allow": ["Bash"]},
            "statusLine": {"command": "bash /home/one/statusline.sh"},
            "env": {"CODEX_HOME": "/home/one/.codex"},
        }
    )

    filtered = json.loads(filter_claude_settings(text))

    assert filtered == {"theme": "dark"}


def test_merge_keeps_the_model_the_machine_is_using() -> None:
    source = json.dumps({"theme": "dark"})
    target = json.dumps({"theme": "light", "model": "claude-fable-5"})

    merged = json.loads(merge_claude_settings(source, target))

    # The model is switched in the UI; apply must not reset that choice.
    assert merged["model"] == "claude-fable-5"
    assert merged["theme"] == "dark"


def test_merge_keeps_the_target_machine_values() -> None:
    source = json.dumps(
        {
            "theme": "dark",
            "statusLine": {"command": "bash /home/one/statusline.sh"},
            "env": {"CODEX_HOME": "/home/one/.codex"},
        }
    )
    target = json.dumps(
        {
            "theme": "light",
            "statusLine": {"command": "bash /home/two/other.sh"},
            "env": {"CODEX_HOME": "/home/two/.codex"},
        }
    )

    merged = json.loads(merge_claude_settings(source, target))

    # Shared preferences follow the repo; machine-local ones stay put.
    assert merged["theme"] == "dark"
    assert merged["statusLine"] == {"command": "bash /home/two/other.sh"}
    assert merged["env"] == {"CODEX_HOME": "/home/two/.codex"}


def test_merge_does_not_introduce_keys_the_machine_lacks() -> None:
    source = json.dumps(
        {"theme": "dark", "env": {"CODEX_HOME": "/home/one/.codex"}}
    )
    target = json.dumps({"theme": "light"})

    merged = json.loads(merge_claude_settings(source, target))

    # A machine with no env block must not inherit another machine's paths;
    # Claude Code sets env without a shell, so "~" would not be expanded.
    assert "env" not in merged
    assert merged["theme"] == "dark"


def test_apply_preserves_live_statusline_and_env(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/settings.json",
        json.dumps(
            {
                "theme": "dark",
                "statusLine": {"command": "bash /home/one/statusline.sh"},
                "env": {"CODEX_HOME": "/home/one/.codex"},
            }
        )
        + "\n",
    )
    live = home_dir / ".claude/settings.json"
    write(
        live,
        json.dumps(
            {
                "theme": "light",
                "statusLine": {"command": "bash /home/two/local.sh"},
            }
        )
        + "\n",
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")
    assert result.returncode == 0, result.stderr + result.stdout

    applied = json.loads(live.read_text(encoding="utf-8"))
    assert applied["theme"] == "dark"
    assert applied["statusLine"] == {"command": "bash /home/two/local.sh"}
    assert "env" not in applied
