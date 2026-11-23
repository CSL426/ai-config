"""command-guard: which commands it refuses, and that a bad rule never blocks."""

import io
import json
import os
import re
from pathlib import Path

import pytest

from ai_config import command_guard, hooks, memory_paths
from ai_config.command_guard import Rule, load_rules, simple_commands, violations
from ai_config.tools.claude import filter_claude_settings, merge_claude_settings

GH = Rule("gh-auth-switch", re.compile(r"gh\s+auth\s+switch\b"), "會切換整台機器的帳號")
PNPM = Rule("pnpm-only", re.compile(r"(npm|yarn)\b"), "用 pnpm", when_file="pnpm-lock.yaml")


def names(command: str, cwd: Path, rules: list[Rule]) -> list[str]:
    return [rule.name for rule, _ in violations(command, cwd, rules)]


@pytest.mark.parametrize("command", [
    "gh auth switch -u other",
    "GH_TOKEN=x gh auth switch",
    "cd /tmp && gh auth switch",
    "echo hi; gh auth switch",
    "true || gh auth switch",
    "env FOO=1 gh auth switch",
    "(gh auth switch)",
    "ls\ngh auth switch",
])
def test_a_ruled_out_command_is_refused_wherever_it_starts(tmp_path: Path, command: str) -> None:
    assert names(command, tmp_path, [GH]) == ["gh-auth-switch"]


@pytest.mark.parametrize("command", [
    'git commit -m "never run gh auth switch"',
    "echo gh auth switch",
    "gh auth status",
    "grep -r 'gh auth switch' .",
    "cat <<'EOF'\ngh auth switch\nEOF",
    "git commit -F - <<EOF\nfix: x\n\ngh auth switch breaks sessions\nEOF\necho done",
])
def test_mentioning_a_command_is_not_running_it(tmp_path: Path, command: str) -> None:
    assert names(command, tmp_path, [GH]) == []


def test_text_after_a_heredoc_is_still_checked(tmp_path: Path) -> None:
    command = "cat <<EOF > notes\nhello\nEOF\ngh auth switch"
    assert names(command, tmp_path, [GH]) == ["gh-auth-switch"]


def test_unreadable_shell_text_is_left_alone(tmp_path: Path) -> None:
    assert simple_commands('echo "unterminated') is None
    assert names('gh auth switch "unterminated', tmp_path, [GH]) == []


def test_when_file_looks_up_to_the_repository_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    nested = project / "packages" / "web"
    nested.mkdir(parents=True)

    assert names("npm install", nested, [PNPM]) == ["pnpm-only"]
    assert names("pnpm install", nested, [PNPM]) == []
    assert names("npm install", tmp_path, [PNPM]) == []


def test_when_file_stops_at_the_repository_root(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)

    assert names("npm install", other, [PNPM]) == []


def test_cd_moves_where_when_file_is_judged(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "pnpm-lock.yaml").write_text("", encoding="utf-8")

    assert names("cd web && npm install", tmp_path, [PNPM]) == ["pnpm-only"]
    assert names("npm install", tmp_path, [PNPM]) == []


@pytest.mark.skipif(os.name == "nt", reason="when_running reads ps, which Windows lacks")
def test_when_running_depends_on_another_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build = Rule(
        "one-build", re.compile(r"docker\s+compose\b.*--build"), "一次一個 build",
        when_running=re.compile(r"docker compose .*--build"),
    )
    me, parent = os.getpid(), os.getppid()
    listing = {"text": f"{me} {parent} acg __command-guard\n{parent} 1 bash -c 'docker compose up --build'\n"}

    class Finished:
        @property
        def stdout(self) -> str:
            return listing["text"]

    monkeypatch.setattr(command_guard.subprocess, "run", lambda *a, **k: Finished())
    # 自己的上層 shell 帶著被檢查的文字,不算「已經有一個在跑」
    assert names("docker compose up -d --build", tmp_path, [build]) == []

    listing["text"] += "4242 1 docker compose up -d --build api\n"
    assert names("docker compose up -d --build", tmp_path, [build]) == ["one-build"]


def _write_rules(data: Path, document: object) -> None:
    (data / "claude").mkdir(parents=True, exist_ok=True)
    (data / "claude" / "command-guard.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8",
    )


def test_a_broken_rule_is_skipped_and_the_rest_still_apply(tmp_path: Path) -> None:
    _write_rules(tmp_path, {"rules": [
        {"name": "bad-regex", "command": "(", "reason": "x"},
        {"name": "no-reason", "command": "ls"},
        "not an object",
        {"name": "gh", "command": r"gh\s+auth\s+switch\b", "reason": "r"},
    ]})

    rules, problems = load_rules(tmp_path)

    assert [rule.name for rule in rules] == ["gh"]
    assert len(problems) == 3


def test_no_rules_file_means_nothing_is_refused(tmp_path: Path) -> None:
    assert load_rules(tmp_path) == ([], [])


@pytest.mark.parametrize("content", ["{not json", "[]", '{"rules": 3}'])
def test_an_unreadable_rules_file_never_blocks(tmp_path: Path, content: str) -> None:
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / "command-guard.json").write_text(content, encoding="utf-8")

    rules, problems = load_rules(tmp_path)

    assert rules == []
    assert problems


def _run(monkeypatch: pytest.MonkeyPatch, data: Path, payload: object) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return command_guard.run_hook([str(data)])


def test_the_hook_exits_2_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _write_rules(tmp_path, {"rules": [
        {"name": "gh-auth-switch", "command": r"gh\s+auth\s+switch\b", "reason": "會影響別的 session"},
    ]})
    payload = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": "gh auth switch"}}

    assert _run(monkeypatch, tmp_path, payload) == 2
    err = capsys.readouterr().err
    assert "gh-auth-switch" in err
    assert "會影響別的 session" in err


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "cwd": "/", "tool_input": {"command": "ls"}},
    {"tool_name": "Edit", "cwd": "/", "tool_input": {"command": "gh auth switch"}},
    {"tool_name": "Bash", "tool_input": {"command": "gh auth switch"}},
    "not a dict",
])
def test_the_hook_lets_everything_else_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object,
) -> None:
    _write_rules(tmp_path, {"rules": [
        {"name": "gh", "command": r"gh\s+auth\s+switch\b", "reason": "r"},
    ]})
    assert _run(monkeypatch, tmp_path, payload) == 0


def test_the_hook_ignores_garbage_on_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{nope"))
    assert command_guard.run_hook([str(tmp_path)]) == 0


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude = tmp_path / ".claude"
    claude.mkdir()
    monkeypatch.setattr(hooks, "CLAUDE_HOME", claude)
    monkeypatch.setattr(memory_paths, "CLAUDE_HOME", claude)
    return claude


def test_the_installed_hook_names_this_machines_database(home: Path) -> None:
    hooks.configure(hooks.COMMAND_GUARD, True)
    document = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    entry = document["hooks"]["PreToolUse"][0]

    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0]["args"][-2:] == ["__command-guard", str(memory_paths.SCRIPT_DIR)]


def test_the_hook_never_reaches_the_database_and_apply_keeps_it(home: Path) -> None:
    hooks.configure(hooks.COMMAND_GUARD, True)
    live = (home / "settings.json").read_text(encoding="utf-8")

    gathered = json.loads(filter_claude_settings(live))
    assert "__command-guard" not in json.dumps(gathered)

    merged = json.loads(merge_claude_settings(json.dumps(gathered), live))
    rows = merged["hooks"]["PreToolUse"]
    assert any(
        h["statusMessage"] == hooks.COMMAND_GUARD.marker for row in rows for h in row["hooks"]
    )


@pytest.mark.parametrize("command", [
    "if gh auth switch; then echo ok; fi",
    "while true; do gh auth switch; done",
    "! gh auth switch",
    "2>/dev/null gh auth switch",
    "2> log gh auth switch",
    "sudo -u other gh auth switch",
    "env -i PATH=/bin gh auth switch",
    "nice -n 5 gh auth switch",
    "{ gh auth switch; }",
])
def test_shell_syntax_in_front_does_not_hide_the_command(tmp_path: Path, command: str) -> None:
    assert names(command, tmp_path, [GH]) == ["gh-auth-switch"]


def test_a_here_string_is_not_a_heredoc(tmp_path: Path) -> None:
    command = "cat <<< EOF\ngh auth switch\nEOF"
    assert names(command, tmp_path, [GH]) == ["gh-auth-switch"]


def test_a_cd_inside_a_subshell_ends_with_it(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "pnpm-lock.yaml").write_text("", encoding="utf-8")

    assert names("(cd web && npm install)", tmp_path, [PNPM]) == ["pnpm-only"]
    assert names("(cd web) && npm install", tmp_path, [PNPM]) == []


def test_a_cd_through_a_variable_keeps_the_directory(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert names("cd $SOMEWHERE && npm install", tmp_path, [PNPM]) == ["pnpm-only"]


def test_when_file_must_be_a_file_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").mkdir()
    assert names("npm install", tmp_path, [PNPM]) == []


@pytest.mark.parametrize("when_file", [
    "/etc/passwd", "../outside.lock", "a/../../b", "C:\\x.lock", "\\\\host\\share\\x", "a\\..\\..\\b",
])
def test_when_file_cannot_point_outside(tmp_path: Path, when_file: str) -> None:
    _write_rules(tmp_path, {"rules": [
        {"name": "x", "command": "npm", "reason": "r", "when_file": when_file},
    ]})
    rules, problems = load_rules(tmp_path)
    assert rules == []
    assert "when_file" in problems[0]


def test_git_bash_drive_paths_become_windows_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_guard.os, "name", "nt")
    assert command_guard._native("/c/project") == "c:/project"
    assert command_guard._native("/c") == "c:/"
    assert command_guard._native("/cache/x") == "/cache/x"


def test_a_non_object_tool_input_is_let_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": ["gh auth switch"]}
    assert _run(monkeypatch, tmp_path, payload) == 0


def test_a_broken_rule_is_shown_to_the_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _write_rules(tmp_path, {"rules": [{"name": "bad", "command": "(", "reason": "x"}]})
    payload = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": "ls"}}

    assert _run(monkeypatch, tmp_path, payload) == 0
    message = json.loads(capsys.readouterr().out)["systemMessage"]
    assert "bad" in message
