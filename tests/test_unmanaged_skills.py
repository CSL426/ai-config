"""Tests for the `status` report on skills ai-config does not manage."""

from pathlib import Path

from test_apply_projection import run_ai_config, write
from test_sync_logic import make_repo

from ai_config.skills import unmanaged_skills


def test_unmanaged_skills_ignores_managed_and_dotfiles(tmp_path: Path) -> None:
    store = tmp_path / "skills"
    (store / "kept").mkdir(parents=True)
    (store / "extra").mkdir()
    (store / ".hidden").mkdir()
    (store / ".ai-config-managed").write_text("kept\n", encoding="utf-8")

    assert unmanaged_skills(store) == ["extra"]


def test_unmanaged_skills_without_manifest_reports_nothing(tmp_path: Path) -> None:
    store = tmp_path / "skills"
    (store / "extra").mkdir(parents=True)

    # No manifest means ai-config has never deployed here; every directory is
    # the user's own, so there is no drift to report.
    assert unmanaged_skills(store) == []


def test_unmanaged_skills_on_missing_directory(tmp_path: Path) -> None:
    assert unmanaged_skills(tmp_path / "absent") == []


def test_status_reports_unmanaged_skill_directory(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/skills/acg/SKILL.md", "---\nname: acg\n---\nbody\n")

    applied = run_ai_config(repo_dir, home_dir, "apply", "codex")
    assert applied.returncode == 0, applied.stderr + applied.stdout

    (home_dir / ".agents/skills/leftover").mkdir(parents=True, exist_ok=True)
    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    output = result.stdout + result.stderr
    assert "Unmanaged skills" in output
    assert "leftover" in output
    assert "acg" not in output.split("Unmanaged skills")[1]


def test_status_reports_clean_when_only_managed_skills_present(
    tmp_path: Path,
) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/skills/acg/SKILL.md", "---\nname: acg\n---\nbody\n")

    applied = run_ai_config(repo_dir, home_dir, "apply", "codex")
    assert applied.returncode == 0, applied.stderr + applied.stdout

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert "No unmanaged skill directories" in result.stdout + result.stderr


def test_status_does_not_prune_the_unmanaged_directory(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/skills/acg/SKILL.md", "---\nname: acg\n---\nbody\n")
    run_ai_config(repo_dir, home_dir, "apply", "codex")

    leftover = home_dir / ".agents/skills/leftover"
    leftover.mkdir(parents=True, exist_ok=True)
    write(leftover / "SKILL.md", "hand written\n")

    run_ai_config(repo_dir, home_dir, "status", "codex")
    run_ai_config(repo_dir, home_dir, "apply", "codex")

    # Reporting drift must never start deleting the user's own skills.
    assert (leftover / "SKILL.md").read_text(encoding="utf-8") == "hand written\n"
