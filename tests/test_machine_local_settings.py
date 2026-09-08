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
            "statusLine": {"command": "bash ~/.claude/statusline.sh"},
            "env": {"CODEX_HOME": "/home/one/.codex"},
            # auto mode 在這台學到的環境描述:內網主機、私有 repo,不能出去
            "autoMode": {"environment": ["NAS at 10.0.0.1"]},
        }
    )

    filtered = json.loads(filter_claude_settings(text))

    # statusLine 跟著同步的腳本走,寫法用 ~ 所以不綁機器
    assert filtered == {
        "theme": "dark",
        "statusLine": {"command": "bash ~/.claude/statusline.sh"},
    }


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
            "permissions": {"allow": ["Read"]},
            "env": {"CODEX_HOME": "/home/one/.codex"},
        }
    )
    target = json.dumps(
        {
            "theme": "light",
            "permissions": {"allow": ["Bash"]},
            "env": {"CODEX_HOME": "/home/two/.codex"},
        }
    )

    merged = json.loads(merge_claude_settings(source, target))

    # Shared preferences follow the repo; machine-local ones stay put.
    assert merged["theme"] == "dark"
    assert merged["permissions"] == {"allow": ["Bash"]}
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


def test_apply_syncs_statusline_but_preserves_env(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/settings.json",
        json.dumps(
            {
                "theme": "dark",
                "statusLine": {"command": "bash ~/.claude/statusline.sh"},
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
    # 腳本跟著 repo 走,指向它的設定也跟著走;新機器 apply 完狀態列就會亮
    assert applied["statusLine"] == {"command": "bash ~/.claude/statusline.sh"}
    assert "env" not in applied
