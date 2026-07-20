"""Tests for `ai-config package`: zipping a shared skill for Claude Desktop."""

import zipfile
from pathlib import Path

from test_apply_projection import copy_runtime_files, run_ai_config, write


def make_shared_skill(repo_dir: Path, source: str, name: str) -> None:
    write(
        repo_dir / f"claude/shared/{source}/{name}/SKILL.md",
        f"---\nname: {name}\ndescription: Test skill.\n---\nBody.\n",
    )


def test_package_lists_available_skills_when_no_name_given(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    make_shared_skill(repo_dir, "both", "demo-skill")

    result = run_ai_config(repo_dir, home_dir, "package")

    assert result.returncode == 0
    assert "demo-skill" in result.stdout


def test_package_builds_zip_with_skill_dir_at_root(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    make_shared_skill(repo_dir, "both", "demo-skill")
    write(repo_dir / "claude/shared/both/demo-skill/references/notes.md", "notes\n")

    result = run_ai_config(repo_dir, home_dir, "package", "demo-skill")

    assert result.returncode == 0
    zip_path = repo_dir / "demo-skill.zip"
    assert zip_path.is_file()

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "demo-skill/SKILL.md" in names
    assert "demo-skill/references/notes.md" in names


def test_package_unknown_skill_fails_with_message(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    make_shared_skill(repo_dir, "both", "demo-skill")

    result = run_ai_config(repo_dir, home_dir, "package", "missing-skill")

    assert result.returncode == 1
    assert "not found" in result.stdout + result.stderr
