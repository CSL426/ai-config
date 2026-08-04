"""Tests for `deploy`: copying managed Claude config into a project directory."""

from pathlib import Path

from test_apply_projection import run_ai_config, write
from test_sync_logic import make_repo


def make_populated_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "claude/settings.json", '{"theme": "dark"}\n')
    write(repo_dir / "claude/rules/common/style.md", "rules\n")
    write(repo_dir / "claude/commands/commit.md", "---\ndescription: x\n---\n")
    write(repo_dir / "claude/skills/acg/SKILL.md", "---\nname: acg\n---\nbody\n")
    return repo_dir, home_dir


def test_deploy_all_copies_every_managed_item(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (project / ".claude/CLAUDE.md").read_text(encoding="utf-8") == "instructions\n"
    assert (project / ".claude/settings.json").is_file()
    assert (project / ".claude/rules/common/style.md").is_file()
    assert (project / ".claude/commands/commit.md").is_file()
    assert (project / ".claude/skills/acg/SKILL.md").is_file()


def test_deploy_selection_copies_only_chosen_items(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    # Menu order is files then dirs: CLAUDE.md is 1, skills is the last entry.
    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="1\ny\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (project / ".claude/CLAUDE.md").is_file()
    assert not (project / ".claude/skills").exists()
    assert not (project / ".claude/rules").exists()


def test_deploy_cancelled_writes_nothing(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="a\nn\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert not (project / ".claude").exists()


def test_deploy_rejects_missing_directory(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(tmp_path / "nope"), input_text="a\ny\n"
    )

    assert result.returncode == 1
    assert "not a directory" in (result.stderr + result.stdout).lower()


def test_deploy_defaults_to_current_directory(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)

    # No path argument: the repo dir itself is the cwd for run_ai_config.
    result = run_ai_config(repo_dir, home_dir, "deploy", input_text="1\ny\n")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (repo_dir / ".claude/CLAUDE.md").is_file()
