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


def make_multi_skill_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    write(repo_dir / "claude/skills/wiki888/SKILL.md", "---\nname: wiki888\n---\nb\n")
    write(repo_dir / "claude/skills/hallmark/SKILL.md", "---\nname: hallmark\n---\nb\n")
    return repo_dir, home_dir


def test_deploy_menu_lists_skills_individually(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)

    result = run_ai_config(repo_dir, home_dir, "deploy", str(tmp_path), input_text="\n")

    assert "skills/acg" in result.stdout
    assert "skills/hallmark" in result.stdout
    assert "skills/wiki888" in result.stdout


def test_deploy_selects_a_single_skill(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    # Menu: 1 CLAUDE.md, 2 settings.json, 3 rules/, 4 commands/,
    # then skills sorted: 5 acg, 6 hallmark, 7 wiki888.
    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="6\ny\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (project / ".claude/skills/hallmark/SKILL.md").is_file()
    assert not (project / ".claude/skills/acg").exists()
    assert not (project / ".claude/skills/wiki888").exists()


def test_deploy_save_as_then_profile_replays_selection(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)
    first = tmp_path / "first"
    first.mkdir()

    saved = run_ai_config(
        repo_dir,
        home_dir,
        "deploy",
        str(first),
        "--save-as",
        "frontend",
        input_text="1 6\ny\n",
    )
    assert saved.returncode == 0, saved.stderr + saved.stdout
    assert (repo_dir / "claude/deploy-profiles.toml").is_file()

    second = tmp_path / "second"
    second.mkdir()
    replayed = run_ai_config(
        repo_dir, home_dir, "deploy", str(second), "--profile", "frontend"
    )

    assert replayed.returncode == 0, replayed.stderr + replayed.stdout
    assert (second / ".claude/CLAUDE.md").is_file()
    assert (second / ".claude/skills/hallmark/SKILL.md").is_file()
    assert not (second / ".claude/skills/acg").exists()


def test_deploy_unknown_profile_fails(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(tmp_path), "--profile", "nope"
    )

    assert result.returncode == 1
    assert "unknown profile" in (result.stdout + result.stderr).lower()


def test_deploy_profile_reports_items_removed_from_repo(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    write(
        repo_dir / "claude/deploy-profiles.toml",
        '[stale]\nitems = ["skills/gone"]\n',
    )
    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), "--profile", "stale"
    )

    assert result.returncode == 1
    assert "skills/gone" in result.stdout + result.stderr
    assert not (project / ".claude").exists()


def test_deploy_rejects_profile_combined_with_save_as(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)

    result = run_ai_config(
        repo_dir,
        home_dir,
        "deploy",
        str(tmp_path),
        "--profile",
        "a",
        "--save-as",
        "b",
    )

    assert result.returncode == 1
    assert "cannot be combined" in (result.stdout + result.stderr).lower()
