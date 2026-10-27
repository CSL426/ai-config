"""Exercise memory push boundaries using disposable Git repositories."""

import subprocess
from pathlib import Path

import pytest
from push_helpers import patch_push

from ai_config import autopush, push_preflight, push_publish
from ai_config.commands import push, sync


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def data_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=main")
    repo = tmp_path / "data"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "claude").mkdir()
    (repo / "claude/settings.json").write_text("{}\n", encoding="utf-8")
    git(repo, "add", "claude")
    git(repo, "commit", "-m", "Initial configuration")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--set-upstream", "origin", "main")
    patch_push(monkeypatch, "SCRIPT_DIR", repo)
    monkeypatch.setattr(sync, "SCRIPT_DIR", repo)
    patch_push(monkeypatch, "configured_remote_provider", lambda: "git")
    patch_push(monkeypatch, "_remote_is_read_only", lambda: False)
    patch_push(monkeypatch, "_ALLOW_SECRET_PATHS", False)
    monkeypatch.setattr(autopush, "SCRIPT_DIR", repo)
    return repo


def tracked_memory(repo: Path) -> Path:
    root = repo / "memory"
    root.mkdir()
    (root / "MEMORY.md").write_text("# Notes\n", encoding="utf-8")
    git(repo, "add", "memory")
    git(repo, "commit", "-m", "Add notes")
    git(repo, "push")
    return root


def test_push_all_without_optional_directories_can_review_and_cancel(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme": "dark"}\n', encoding="utf-8")
    reviewed = []

    def decline(prompt: str) -> bool:
        reviewed.append(git(data_repo, "diff", "--cached", "--name-only"))
        return False

    patch_push(monkeypatch, "confirm_prompt", decline)
    # 沒拿到確認就沒推,離開碼要讓呼叫端分得出來
    assert push.do_push("all") == 1
    assert reviewed == ["claude/settings.json"]
    assert git(data_repo, "diff", "--cached", "--name-only") == ""
    assert git(data_repo, "diff", "--name-only") == "claude/settings.json"
    assert not (data_repo / "memory").exists()


def test_nothing_to_push_is_a_no_op_not_a_failure(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """暫存後沒有實質差異(例如純換行變動)是 no-op,不是失敗。"""
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme": "dark"}\n', encoding="utf-8")
    # git status 看得到修改,但暫存差異是空的
    patch_push(monkeypatch, "_staged_diff", lambda: "")

    assert push.do_push("claude") == 0


@pytest.mark.parametrize("scope", ["memory", "all"])
def test_missing_memory_root_refuses_push_before_staging(
    data_repo: Path, scope: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").unlink()
    root.rmdir()

    assert push.do_push(scope) == 1
    assert "Memory directory is missing" in capsys.readouterr().err
    assert git(data_repo, "diff", "--cached", "--name-only") == ""
    assert git(data_repo, "diff", "--name-only") == "memory/MEMORY.md"


def test_memory_root_disappearing_after_preflight_refuses_staging(
    data_repo: Path,
) -> None:
    root = tracked_memory(data_repo)
    assert push_preflight._push_preflight(["memory"]) is not None
    (root / "MEMORY.md").unlink()
    root.rmdir()

    assert push_publish._stage_push_changes(["memory"]) is None
    assert git(data_repo, "diff", "--cached", "--name-only") == ""


def test_individual_memory_deletion_allowed_while_root_exists(
    data_repo: Path,
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").unlink()

    reviewed = push_publish._stage_push_changes(["memory"])
    assert reviewed is not None
    assert "deleted file" in reviewed
    assert push_publish._staged_push_matches(["memory"], reviewed)

    # An empty root can vanish without changing the Git diff after review.
    root.rmdir()
    assert not push_publish._staged_push_matches(["memory"], reviewed)
    assert push_publish._unstage_tools(push_preflight._push_scopes("all"))
    assert git(data_repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize(
    ("relative", "content", "message"),
    [
        ("memory/auth.json", "{}\n", "Credential files"),
        ("memory/MEMORY.md", "password = example\n", "Potential credential"),
        ("notes.md", "outside\n", "Unexpected repository changes"),
    ],
)
def test_optional_scope_filter_keeps_staged_safety_checks(
    data_repo: Path,
    relative: str,
    content: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Changed notes\n", encoding="utf-8")
    (data_repo / relative).write_text(content, encoding="utf-8")

    assert push_publish._stage_push_changes(push_preflight._push_scopes("all")) is None
    assert message in capsys.readouterr().err
    assert git(data_repo, "diff", "--cached", "--name-only") == ""


def test_memory_push_preserves_other_pre_staged_changes(
    data_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Changed notes\n", encoding="utf-8")
    (data_repo / "claude/settings.json").write_text(
        '{"theme": "dark"}\n', encoding="utf-8"
    )
    git(data_repo, "add", "claude")

    assert push.do_push("memory") == 1
    assert "pre-staged changes" in capsys.readouterr().err
    assert git(data_repo, "diff", "--cached", "--name-only") == "claude/settings.json"


def test_memory_push_refuses_unpublished_configuration_commits(
    data_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked_memory(data_repo)
    (data_repo / "claude/settings.json").write_text(
        '{"theme": "dark"}\n', encoding="utf-8"
    )
    git(data_repo, "add", "claude")
    git(data_repo, "commit", "-m", "Unpublished configuration")

    assert push.do_push("memory") == 1
    assert "outside the selected tools" in capsys.readouterr().err
    assert git(data_repo, "rev-list", "--count", "@{upstream}..HEAD") == "1"


def test_a_config_change_does_not_cancel_the_scheduled_memory_push(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nightly run pushes memory only; other directories are not its business.

    A settings edit left uncommitted once cancelled the whole scheduled run,
    with nobody watching, and the advice printed was to run the very command
    that had just failed.
    """
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Notes\n\nnew line\n", encoding="utf-8")
    (data_repo / "claude/settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    staged = []

    def accept(prompt: str) -> bool:
        staged.append(git(data_repo, "diff", "--cached", "--name-only"))
        return True

    patch_push(monkeypatch, "confirm_prompt", accept)

    assert push.do_push("memory") == 0
    # 只收記憶,設定的改動原封不動留在工作區
    assert staged == ["memory/MEMORY.md"]
    assert git(data_repo, "diff", "--name-only") == "claude/settings.json"


def test_another_tools_change_still_cancels_a_hand_picked_scope(
    data_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Choosing one tool by hand is different: a missed directory is worth saying."""
    tracked_memory(data_repo)
    (data_repo / "claude/settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    (data_repo / "codex").mkdir()
    (data_repo / "codex/config.toml").write_text("x = 1\n", encoding="utf-8")

    assert push.do_push("claude") == 1
    assert "outside the selected tools" in capsys.readouterr().err


SECRET_NOTE = "memory/projects/o--r/journal/today-2026-10-19.done.md"


def _token_line() -> str:
    # 在執行時才組出來:原始碼裡的假憑證字面值會被 gitleaks 擋下
    return "UPDATE_TOKEN=" + "abc123def456" + "\n"


def _write_secret_note(repo: Path, text: str) -> Path:
    secret = repo / SECRET_NOTE
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(text, encoding="utf-8")
    return secret


def _record_review(monkeypatch: pytest.MonkeyPatch, repo: Path) -> "list[str]":
    """Accept the push and keep what was staged when the question was asked."""
    shown: list[str] = []

    def accept(prompt: str) -> bool:
        shown.append(git(repo, "diff", "--cached", "--name-only"))
        return True

    patch_push(monkeypatch, "confirm_prompt", accept)
    return shown


def test_a_memory_push_leaves_out_a_note_with_a_token_and_sends_the_rest(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """DESKTOP-CAO4ENQ: one journal line with a token kept the whole notebook local."""
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Notes\n\na new handoff pointer\n", encoding="utf-8")
    secret = _write_secret_note(data_repo, "## 10:00\n.env 缺 " + _token_line())
    shown = _record_review(monkeypatch, data_repo)

    assert push.do_push("memory") == 0

    out = capsys.readouterr()
    assert SECRET_NOTE in out.out + out.err
    assert shown == ["memory/MEMORY.md"], "確認時看到的清單裡就沒有它"
    assert git(data_repo, "rev-list", "--count", "@{upstream}..HEAD") == "0"
    assert "a new handoff pointer" in git(data_repo, "show", "origin/main:memory/MEMORY.md")
    assert git(data_repo, "ls-files", "--others", "--exclude-standard") == SECRET_NOTE
    assert autopush.held_notes() == [SECRET_NOTE]

    # 值拿掉之後下一次上傳就帶上它,也不再算留下的
    secret.write_text("## 10:00\n.env 缺 UPDATE_TOKEN(值已移除)\n", encoding="utf-8")
    assert push.do_push("memory") == 0
    assert SECRET_NOTE in git(data_repo, "ls-tree", "-r", "--name-only", "origin/main")
    assert autopush.held_notes() == []


def test_only_a_note_with_a_token_changed_leaves_nothing_to_push(
    data_repo: Path,
) -> None:
    tracked_memory(data_repo)
    _write_secret_note(data_repo, _token_line())
    head = git(data_repo, "rev-parse", "HEAD")

    assert push.do_push("memory") == 0
    assert git(data_repo, "rev-parse", "HEAD") == head
    assert autopush.held_notes() == [SECRET_NOTE]


def test_a_push_that_names_a_tool_still_stops_on_a_token(
    data_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A tool's settings go together, so leaving one file out could break the rest."""
    tracked_memory(data_repo)
    _write_secret_note(data_repo, _token_line())
    (data_repo / "claude/settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    held: set[str] = set()

    assert push_publish._stage_push_changes(push_preflight._push_scopes("all"), held) is None
    assert "push cancelled" in capsys.readouterr().err
    assert held == set()


def test_an_index_with_a_token_stops_the_whole_push(
    data_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Pushing notes without the index that links them would hide them from other machines."""
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Notes\n" + _token_line(), encoding="utf-8")
    (root / "topics").mkdir()
    (root / "topics/new.md").write_text("a new note\n", encoding="utf-8")

    assert push.do_push("memory") == 1
    assert "push cancelled" in capsys.readouterr().err
    assert git(data_repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("target", ["memory/topics/archive/deploy.md", "memory/topics/renamed.md"])
def test_a_moved_note_with_a_token_keeps_its_old_copy_on_the_remote(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    """Holding back only the new name would push the deletion of the old one."""
    root = tracked_memory(data_repo)
    (root / "topics").mkdir()
    body = "deploy notes, with enough lines to look like the same file\n" * 8
    (root / "topics/deploy.md").write_text(body, encoding="utf-8")
    git(data_repo, "add", "memory")
    git(data_repo, "commit", "-m", "note")
    git(data_repo, "push")
    moved = data_repo / target
    moved.parent.mkdir(parents=True, exist_ok=True)
    (root / "topics/deploy.md").rename(moved)
    moved.write_text(body + _token_line(), encoding="utf-8")
    (root / "MEMORY.md").write_text("# Notes\n- updated\n", encoding="utf-8")
    shown = _record_review(monkeypatch, data_repo)

    assert push.do_push("memory") == 0
    assert shown == ["memory/MEMORY.md"]
    assert "memory/topics/deploy.md" in git(data_repo, "ls-tree", "-r", "--name-only", "origin/main")


def test_the_review_does_not_list_a_left_out_note(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Notes\n- updated\n", encoding="utf-8")
    _write_secret_note(data_repo, _token_line())
    patch_push(monkeypatch, "confirm_prompt", lambda prompt: False)

    push.do_push("memory")

    review = capsys.readouterr().out.split("Configuration changes to commit:", 1)[1]
    review = review.split("These notes look like", 1)[0]
    assert "today-2026-10-19" not in review.split("diff --git", 1)[0]


def test_the_review_lists_what_is_staged_with_names_unquoted(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """git status quotes a non-ASCII name; the review reads the index with -z instead."""
    root = tracked_memory(data_repo)
    (root / "topics").mkdir()
    (root / "topics/部署.md").write_text("a note\n", encoding="utf-8")
    _write_secret_note(data_repo, _token_line())
    patch_push(monkeypatch, "confirm_prompt", lambda prompt: False)

    push.do_push("memory")

    review = capsys.readouterr().out.split("Configuration changes to commit:", 1)[1]
    listed = review.split("diff --git", 1)[0]
    assert "A  memory/topics/部署.md" in listed
    assert "today-2026-10-19" not in listed.split("These notes look like", 1)[0]


def test_a_control_character_in_a_name_cannot_forge_the_review() -> None:
    shown = push_preflight._shown_path("memory/a\nM  memory/innocent.md\x1b[2K")

    assert "\n" not in shown and "\x1b" not in shown
    assert push_preflight._shown_path("memory/topics/部署.md") == "memory/topics/部署.md"


def test_a_held_note_does_not_block_a_tool_push(
    data_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A4000, 2026-10-27: push codex said to push memory first, and memory push kept the note.

    The note is left out by memory push on purpose, so a push that does not
    select memory has nothing to wait for. Any other memory change still stops it.
    """
    tracked_memory(data_repo)
    _write_secret_note(data_repo, _token_line())
    (data_repo / "claude/settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    patch_push(monkeypatch, "confirm_prompt", lambda prompt: True)

    assert push.do_push("claude") == 0

    assert "claude/settings.json" in git(data_repo, "show", "--name-only", "--format=", "origin/main")
    assert git(data_repo, "ls-files", "--others", "--exclude-standard") == SECRET_NOTE
    assert "疑似含憑證的筆記留在本機" in capsys.readouterr().out


def test_an_ordinary_memory_change_still_blocks_a_tool_push(
    data_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tracked_memory(data_repo)
    (root / "MEMORY.md").write_text("# Notes\n- unpushed\n", encoding="utf-8")
    (data_repo / "claude/settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")

    assert push.do_push("claude") == 1
    assert "outside the selected tools" in capsys.readouterr().err
