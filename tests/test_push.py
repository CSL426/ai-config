"""push: review, credential and scope checks, publishing, and the scheduled path."""

import os
import subprocess
from pathlib import Path

import pytest
from data_repo_helpers import (
    commit_and_push_settings,
    configure_git_identity,
    create_data_remote,
    run_data_alias_cli,
    run_data_cli,
    run_git,
)
from push_helpers import patch_push

from ai_config import push_preflight, push_publish, push_review


def test_push_collects_commits_and_pushes_selected_tool(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"dark"}\n', encoding="utf-8")

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Configuration changes to commit" in result.stdout
    assert "Local configuration committed and pushed" in result.stdout
    assert run_git(remote, "show", "HEAD:claude/settings.json") == ('{"theme":"dark"}')
    assert run_git(data_repo, "status", "--porcelain=v1") == ""
    assert run_git(data_repo, "log", "-1", "--pretty=%s") == (
        "chore: update claude settings"
    )


def test_push_commits_new_file_after_review(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    claude_home = home / ".claude"
    claude_home.mkdir()
    (claude_home / "settings.json").write_text("{}", encoding="utf-8")
    (claude_home / "CLAUDE.md").write_text(
        "new reviewed instructions\n",
        encoding="utf-8",
    )

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "+new reviewed instructions" in result.stdout
    assert run_git(remote, "show", "HEAD:claude/CLAUDE.md") == (
        "new reviewed instructions"
    )
    assert run_git(data_repo, "status", "--porcelain=v1") == ""


def test_push_force_skips_the_confirmation_without_a_terminal(
    tmp_path: Path,
) -> None:
    # agent 與腳本沒有終端機:--force 讓確認自動通過,全程走得完
    _remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")

    result = run_data_cli(data_repo, home, "push", "claude", "--force")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "committed and pushed" in result.stdout
    assert run_git(data_repo, "status", "--porcelain=v1") == ""


@pytest.mark.parametrize("input_text", ["n\n", ""])
def test_push_cancel_leaves_collected_changes_unstaged(
    tmp_path: Path, input_text: str
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text=input_text,
    )

    assert result.returncode == 1, result.stderr + result.stdout
    assert "configuration changes remain unstaged" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "status", "--short") == "M claude/settings.json"
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_review_displays_new_file_content(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    claude_home = home / ".claude"
    claude_home.mkdir()
    (claude_home / "settings.json").write_text("{}", encoding="utf-8")
    (claude_home / "CLAUDE.md").write_text(
        "new instructions visible in review\n",
        encoding="utf-8",
    )

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 1, result.stderr + result.stdout
    assert "new file mode" in result.stdout
    assert "+new instructions visible in review" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "status", "--short") == "?? claude/CLAUDE.md"


def test_a_forced_push_lists_the_files_but_not_their_content(tmp_path: Path) -> None:
    """Nobody reads a forced push; its output lands in the nightly log.

    One Windows nightly.log carried the full text of the day's journals
    that way, a second copy of them outside the data repository.
    """
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    claude_home = home / ".claude"
    claude_home.mkdir()
    (claude_home / "settings.json").write_text("{}", encoding="utf-8")
    (claude_home / "CLAUDE.md").write_text("private working notes\n", encoding="utf-8")

    result = run_data_cli(data_repo, home, "push", "claude", "--force")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "CLAUDE.md" in result.stdout
    assert "private working notes" not in result.stdout
    # 已經提交了,diff --cached 看不到東西
    assert "show HEAD" in result.stdout


def test_acg_alias_runs_push_command(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")

    result = run_data_alias_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 1, result.stderr + result.stdout
    assert "Commit and push these changes?" in result.stdout
    assert "configuration changes remain unstaged" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""


def test_push_refuses_dirty_path_outside_selected_tool(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")
    (data_repo / "notes.txt").write_text("uncommitted\n", encoding="utf-8")

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "outside the selected tools" in result.stderr
    assert "notes.txt" in result.stdout
    assert (data_repo / "claude/settings.json").read_text(encoding="utf-8") == "{}"


def test_push_reviews_and_publishes_existing_uncommitted_changes(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    repo_settings = data_repo / "claude/settings.json"
    repo_settings.write_text('{"theme":"collected"}\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    live_settings = home / ".claude/settings.json"
    live_settings.parent.mkdir()
    live_settings.write_text('{"theme":"live"}\n', encoding="utf-8")

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Reviewing existing uncommitted configuration changes" in result.stdout
    assert '-{"theme":"collected"}' not in result.stdout
    assert '+{"theme":"collected"}' in result.stdout
    assert "Commit message: chore: update claude settings" in result.stdout
    assert "Init Claude" not in result.stdout
    assert run_git(remote, "show", "HEAD:claude/settings.json") == (
        '{"theme":"collected"}'
    )
    assert run_git(data_repo, "status", "--short") == ""


def test_push_commit_message_uses_tools_and_changed_json_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import sync as sync_cli

    _, data_repo = create_data_remote(tmp_path)
    claude_settings = data_repo / "claude/settings.json"
    claude_settings.write_text('{"model":"claude-sonnet"}\n', encoding="utf-8")
    agy_settings = data_repo / "agy/settings.json"
    agy_settings.parent.mkdir()
    agy_settings.write_text('{"model":"gemini-flash"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json", "agy/settings.json")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)

    assert (
        push_review._proposed_push_commit_message(
            ["agy/settings.json", "claude/settings.json"]
        )
        == "chore: update claude and agy model settings"
    )


def test_push_commit_message_identifies_shared_skill() -> None:

    assert (
        push_review._proposed_push_commit_message(
            [
                "claude/shared/both/ci-check/SKILL.md",
                "claude/shared/both/ci-check/scripts/check.py",
            ]
        )
        == "chore: update ci-check shared skill"
    )


def test_push_cancel_preserves_existing_changes_unstaged(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"collected"}\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 1, result.stderr + result.stdout
    assert "configuration changes remain unstaged" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_refuses_pre_staged_changes_without_altering_index(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"staged"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "pre-staged changes" in result.stderr
    assert run_git(data_repo, "diff", "--cached", "--name-only") == (
        "claude/settings.json"
    )
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_refuses_uncommitted_credential_file(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    credential = data_repo / "claude/auth.json"
    credential.write_text("placeholder\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "credential files" in result.stderr
    assert run_git(data_repo, "status", "--short") == "?? claude/auth.json"
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/auth.json" not in remote_tree


def test_push_refuses_dirty_repository_with_ahead_commit(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/CLAUDE.md"
    instructions.write_text("local commit\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/CLAUDE.md")
    run_git(data_repo, "commit", "-m", "local commit")
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"dirty"}\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "both uncommitted changes and unpublished" in result.stderr
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_refuses_dirty_repository_behind_upstream(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"dirty"}\n', encoding="utf-8")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote update")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "ahead 0, behind 1" in result.stderr
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"


def test_push_refuses_existing_git_operation(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / ".git/rebase-merge").mkdir()
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "rebase in progress" in result.stderr


def test_push_refuses_branch_behind_upstream(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote update")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "ahead 0, behind 1" in result.stderr
    assert "pull before pushing" in result.stdout


def test_push_rejects_ahead_commit_outside_selected_tool(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "local.txt").write_text("local commit\n", encoding="utf-8")
    run_git(data_repo, "add", "local.txt")
    run_git(data_repo, "commit", "-m", "local only")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "outside the selected tools" in result.stderr
    assert "local.txt" in result.stdout


def test_push_publishes_reviewed_ahead_commit_without_gathering(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/CLAUDE.md"
    instructions.write_text("reviewed local instructions\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/CLAUDE.md")
    run_git(data_repo, "commit", "-m", "fix: local instructions")
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "fix: local instructions" in result.stdout
    assert "+reviewed local instructions" in result.stdout
    assert "Push these existing local commits?" in result.stdout
    assert "Existing local commits pushed" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "rev-list", "--count", "@{upstream}..HEAD") == "0"
    assert (
        run_git(remote, "show", "HEAD:claude/CLAUDE.md")
        == "reviewed local instructions"
    )


def test_acg_push_can_cancel_reviewed_ahead_commit(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/CLAUDE.md"
    instructions.write_text("keep this local\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/CLAUDE.md")
    run_git(data_repo, "commit", "-m", "fix: local only")
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_alias_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Cancelled; existing local commits were not pushed" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "rev-list", "--count", "@{upstream}..HEAD") == "1"
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/CLAUDE.md" not in remote_tree


def test_push_rejects_secret_removed_by_later_ahead_commit(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text(
        '{"github_token":"not-a-real-token"}\n',
        encoding="utf-8",
    )
    run_git(data_repo, "add", "claude/settings.json")
    run_git(data_repo, "commit", "-m", "local secret")
    settings.write_text('{"theme":"safe"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    run_git(data_repo, "commit", "-m", "remove local secret")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "all", input_text="y\n")

    assert result.returncode != 0
    assert "Potential credential content exists" in result.stderr
    assert "not-a-real-token" not in result.stdout + result.stderr
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_rejects_credential_file_in_ahead_commit(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    credential = data_repo / "claude/auth.json"
    credential.write_text("placeholder\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/auth.json")
    run_git(data_repo, "commit", "-m", "local credential")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "all", input_text="y\n")

    assert result.returncode != 0
    assert "credential files" in result.stderr
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/auth.json" not in remote_tree


def test_push_rejects_merge_commit_in_ahead_range(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    base_branch = run_git(data_repo, "branch", "--show-current")
    run_git(data_repo, "checkout", "-b", "local-side")
    side_file = data_repo / "claude/side.md"
    side_file.write_text("side\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/side.md")
    run_git(data_repo, "commit", "-m", "local side")
    run_git(data_repo, "checkout", base_branch)
    base_file = data_repo / "claude/base.md"
    base_file.write_text("base\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/base.md")
    run_git(data_repo, "commit", "-m", "local base")
    run_git(data_repo, "merge", "--no-ff", "local-side", "-m", "local merge")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "all", input_text="y\n")

    assert result.returncode != 0
    assert "contains a merge commit" in result.stderr
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/base.md" not in remote_tree
    assert "claude/side.md" not in remote_tree


def test_ahead_push_rejects_upstream_change_after_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import sync as sync_cli

    remote, data_repo = create_data_remote(tmp_path)
    local_file = data_repo / "claude/local.md"
    local_file.write_text("local\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/local.md")
    run_git(data_repo, "commit", "-m", "local update")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)
    snapshot = push_preflight._push_snapshot()
    assert snapshot is not None
    commits = push_preflight._ahead_commits(snapshot)
    assert commits is not None

    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote update")

    assert not push_preflight._ahead_push_matches(
        snapshot,
        ["claude"],
        commits,
    )
    assert "upstream changed after review" in capsys.readouterr().err


def test_push_refuses_detached_head(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    run_git(data_repo, "checkout", "--detach")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "detached HEAD" in result.stderr


def test_push_refuses_branch_without_upstream(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    run_git(data_repo, "branch", "--unset-upstream")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "has no upstream" in result.stderr


def test_push_credential_scan_rejects_staged_credential_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config.commands import sync as sync_cli

    _, data_repo = create_data_remote(tmp_path)
    credential = data_repo / "claude/auth.json"
    credential.write_text("not-a-real-token\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/auth.json")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)

    assert push_preflight._staged_credentials() == ["claude/auth.json"]


def test_push_rejects_staged_path_outside_selected_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import sync as sync_cli

    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "notes.txt").write_text("concurrent staging\n", encoding="utf-8")
    run_git(data_repo, "add", "notes.txt")
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)

    assert push_publish._stage_push_changes(["claude"]) is None
    captured = capsys.readouterr()
    assert "outside the selected tools" in captured.err
    assert "notes.txt" in captured.out
    assert run_git(data_repo, "diff", "--cached", "--name-only") == "notes.txt"
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"


def test_push_rejects_potential_credential_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import sync as sync_cli

    _, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"github_token":"not-a-real-token"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)

    assert not push_preflight._validate_staged_push(["claude"])
    captured = capsys.readouterr()
    assert "Potential credential content" in captured.err
    assert "claude/settings.json" in captured.out
    assert "not-a-real-token" not in captured.out + captured.err


def test_push_scans_staged_blob_with_unicode_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import sync as sync_cli

    _, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/機密說明.md"
    instructions.write_text(
        "Never store secrets here.\ngithub_token = placeholder\n",
        encoding="utf-8",
    )
    run_git(data_repo, "add", "claude/機密說明.md")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)

    assert push_preflight._staged_secret_paths() == ["claude/機密說明.md"]


def test_push_rejects_root_file_named_like_selected_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import sync as sync_cli

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    (repo / "claude").write_text("not a tool directory\n", encoding="utf-8")
    run_git(repo, "add", "claude")
    patch_push(monkeypatch, "SCRIPT_DIR", repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", repo)

    assert push_preflight._staged_paths_outside(["claude"]) == ["claude"]


def test_push_rejects_restaged_content_changed_after_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import sync as sync_cli

    _, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"reviewed"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)
    reviewed_diff = push_preflight._staged_diff()
    assert reviewed_diff is not None

    settings.write_text('{"theme":"changed"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")

    assert not push_publish._staged_push_matches(["claude"], reviewed_diff)
    assert "changed after review" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="requires an executable POSIX Git hook")
def test_push_rolls_back_commit_changed_by_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import sync as sync_cli

    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"reviewed"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    parent = run_git(data_repo, "rev-parse", "HEAD")
    patch_push(monkeypatch, "SCRIPT_DIR", data_repo)
    monkeypatch.setattr(sync_cli, "SCRIPT_DIR", data_repo)
    reviewed_diff = push_preflight._staged_diff()
    assert reviewed_diff is not None

    hook = data_repo / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"theme\":\"changed-by-hook\"}' "
        "> claude/settings.json\n"
        "git add claude/settings.json\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    assert (
        push_publish._commit_and_push(
            "chore: sync claude configuration",
            ["claude"],
            reviewed_diff,
        )
        == 1
    )
    assert run_git(data_repo, "rev-parse", "HEAD") == parent
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"
    captured = capsys.readouterr()
    assert "differed from the reviewed snapshot" in captured.err
    assert "rolled back and not pushed" in captured.out


def test_push_unstages_selected_tools_when_confirmation_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config.commands import push as main_cli

    status = subprocess.CompletedProcess([], 0, " M claude/settings.json\n", "")
    patch_push(monkeypatch, "_push_preflight",
        lambda selected: push_preflight._PushPreflight(ahead=0, has_changes=False),
    )
    patch_push(monkeypatch, "_init_tools", lambda tool: True)
    patch_push(monkeypatch, "_run_repo_git", lambda *args: status)
    patch_push(monkeypatch, "_selected_tools", lambda tool: ["claude"])
    patch_push(monkeypatch, "_stage_push_changes",
        lambda selected: "reviewed diff\n",
    )

    def interrupt_review(*args: object) -> bool:
        raise KeyboardInterrupt

    restored: list[list[str]] = []
    patch_push(monkeypatch, "_review_and_confirm_push", interrupt_review)
    patch_push(monkeypatch, "_unstage_tools",
        lambda selected: restored.append(selected) is None,
    )

    with pytest.raises(KeyboardInterrupt):
        main_cli.do_push("claude")
    assert restored == [["claude"]]


def test_push_reports_failed_unstage_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import push as main_cli

    status = subprocess.CompletedProcess([], 0, " M claude/settings.json\n", "")
    patch_push(monkeypatch, "_push_preflight",
        lambda selected: push_preflight._PushPreflight(ahead=0, has_changes=False),
    )
    patch_push(monkeypatch, "_init_tools", lambda tool: True)
    patch_push(monkeypatch, "_run_repo_git", lambda *args: status)
    patch_push(monkeypatch, "_selected_tools", lambda tool: ["claude"])
    patch_push(monkeypatch, "_stage_push_changes",
        lambda selected: "reviewed diff\n",
    )
    patch_push(monkeypatch, "_review_and_confirm_push",
        lambda *args: False,
    )
    patch_push(monkeypatch, "_unstage_tools", lambda selected: False)

    assert main_cli.do_push("claude") == 1
    assert "failed to restore" in capsys.readouterr().err


def test_push_no_changes_does_not_create_commit(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")

    result = run_data_cli(data_repo, home, "push", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No local configuration changes to push" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before


def test_a_scheduled_push_says_it_was_scheduled() -> None:
    """Three machines push the same subject; only the clock told them apart.

    "chore: sync ai tool configuration" at 04:00 and again at 04:10 reads
    as two people doing the same thing, and answering "who pushed this,
    and did I ask for it" meant knowing each machine's slot by heart.
    """

    scheduled = push_review._proposed_push_commit_message(
        ["claude/settings.json"], scheduled=True,
    )
    by_hand = push_review._proposed_push_commit_message(["claude/settings.json"])

    assert scheduled.splitlines()[0] == by_hand.splitlines()[0]
    assert "Scheduled-By: acg autopush" in scheduled
    assert "Scheduled-By" not in by_hand
