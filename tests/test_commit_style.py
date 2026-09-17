"""The commit subject check: what it refuses, and that it never travels."""

import json
from pathlib import Path

import pytest

from ai_config import commit_style
from ai_config.tools.claude import filter_claude_settings, merge_claude_settings


def judge(command: str) -> "list[str] | None":
    subject = commit_style.commit_subject(command)
    return None if subject is None else commit_style.problems(subject)


@pytest.mark.parametrize("command", [
    'git commit -m "fix(memory): keep the clock local"',
    'git commit -m "chore: release 1.2.3"',
    'git commit -m"feat(gui): add a button"',
    'git commit --message="docs: explain the hook"',
    'git -C /repo commit -m "ci: pin the runner image"',
])
def test_a_conventional_subject_passes(command: str) -> None:
    assert judge(command) == []


@pytest.mark.parametrize("command", [
    'git commit -m "updated some files"',
    'git commit -m "fix memory clock"',
    'git commit -m "style: reformat"',
    'git commit -m "fix(memory): keep the clock local."',
])
def test_a_subject_off_convention_is_refused(command: str) -> None:
    assert judge(command)


@pytest.mark.parametrize("command", [
    "git commit -F /tmp/msg.txt",
    "git commit --amend --no-edit",
    'git commit -m "$MSG"',
    "git status",
    'echo "git commit -m bad"',
])
def test_what_cannot_be_read_is_not_judged(command: str) -> None:
    assert judge(command) is None


def test_a_body_written_as_a_heredoc_is_read() -> None:
    good = 'git commit -m "$(cat <<\'EOF\'\nfix(cli): stop the prompt\n\nWhy, not what.\nEOF\n)"'
    bad = 'git commit -m "$(cat <<\'EOF\'\nupdated stuff\n\nMore text.\nEOF\n)"'
    assert judge(good) == []
    assert judge(bad)


def test_the_subject_limit_is_exact() -> None:
    assert judge(f'git commit -m "fix(x): {"a" * 64}"') == []
    assert judge(f'git commit -m "fix(x): {"a" * 65}"')


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude = tmp_path / ".claude"
    claude.mkdir()
    # hook 的安裝與剝除都住在註冊表裡,改那裡就好
    monkeypatch.setattr(commit_style.hooks, "CLAUDE_HOME", claude)
    monkeypatch.setattr(commit_style.hooks.memory, "CLAUDE_HOME", claude)
    return claude


def test_enable_is_idempotent_and_disable_removes_it(home: Path) -> None:
    assert commit_style.status() == {"installed": False}
    assert commit_style.configure(True) == {"installed": True}
    once = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert commit_style.configure(True) == {"installed": True}
    assert json.loads((home / "settings.json").read_text(encoding="utf-8")) == once
    assert commit_style.configure(False) == {"installed": False}
    assert "hooks" not in json.loads((home / "settings.json").read_text(encoding="utf-8"))


def test_someone_elses_hooks_survive_both_ways(home: Path) -> None:
    (home / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "theirs"}]},
    ]}}), encoding="utf-8")

    commit_style.configure(True)
    commit_style.configure(False)

    rows = json.loads((home / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert rows == [{"matcher": "Bash", "hooks": [{"type": "command", "command": "theirs"}]}]


def test_the_hook_never_reaches_the_database(home: Path) -> None:
    """The whole point: a path naming this machine must not be shared."""
    commit_style.configure(True)
    live = (home / "settings.json").read_text(encoding="utf-8")
    assert "__commit-style" in live

    gathered = json.loads(filter_claude_settings(live))

    assert "__commit-style" not in json.dumps(gathered)
    assert gathered.get("hooks", {}) == {}


def test_apply_keeps_this_machines_hook(home: Path) -> None:
    commit_style.configure(True)
    live = (home / "settings.json").read_text(encoding="utf-8")
    # model 是機器本地欄位,不會從資料庫套過來;拿一個真的會同步的來比
    incoming = json.dumps({"statusLine": {"type": "command", "command": "shared"}})

    merged = json.loads(merge_claude_settings(incoming, live))

    assert merged["statusLine"]["command"] == "shared"
    hooks = merged["hooks"]["PreToolUse"][0]["hooks"]
    assert any(h["statusMessage"] == commit_style.hooks.COMMIT_STYLE.marker for h in hooks)
