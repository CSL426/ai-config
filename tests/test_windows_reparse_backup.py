"""Windows safety: reparse points, junctions, symlinks and backups."""

import json
import os
import shutil
import stat
from pathlib import Path

import pytest
from windows_test_helpers import (
    copy_runtime_files,
    run_script,
    snapshot_tree,
    write,
    write_skills_ownership,
)


def test_init_claude_preflights_nested_managed_reparse_before_repo_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-rules"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    write(repo_dir / "claude/rules/stale.md", "stale rule\n")
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")
    write(home_dir / ".claude/rules/current.md", "current rule\n")
    write(external / "sensitive.md", "external sensitive rule\n")
    (home_dir / ".claude/rules/nested").symlink_to(
        external,
        target_is_directory=True,
    )
    before_repo = snapshot_tree(repo_dir)
    before_external = snapshot_tree(external)

    result = run_script(repo_dir, home_dir, "init", "claude")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(external) == before_external
    assert not (repo_dir / "claude/rules/nested").exists()


def test_init_codex_rejects_reparse_config_without_mutating_repo(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-config.toml"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    write(external, 'model = "sensitive-external"\n')
    config = home_dir / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.symlink_to(external)
    before_repo = snapshot_tree(repo_dir)
    before_external = external.read_bytes()

    result = run_script(repo_dir, home_dir, "init", "codex")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert external.read_bytes() == before_external
    assert b"sensitive-external" not in (repo_dir / "codex/config.toml").read_bytes()


def test_init_agy_rejects_reparse_settings_without_mutating_repo(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-settings.json"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"repo"}\n')
    write(external, '{"token":"sensitive-external"}\n')
    settings = home_dir / ".gemini/antigravity-cli/settings.json"
    settings.parent.mkdir(parents=True)
    settings.symlink_to(external)
    before_repo = snapshot_tree(repo_dir)
    before_external = external.read_bytes()

    result = run_script(repo_dir, home_dir, "init", "agy")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert external.read_bytes() == before_external
    assert b"sensitive-external" not in (repo_dir / "agy/settings.json").read_bytes()


def test_list_counts_only_known_nonhidden_files_and_completed_backups(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "claude/rules/current.md", "rule\n")
    write(repo_dir / "claude/.hidden.json", "hidden\n")
    write(repo_dir / "claude/.hidden/secret.md", "hidden nested\n")
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    write(repo_dir / "codex/.credentials.json", "credential\n")
    (repo_dir / "agy").mkdir()
    write(repo_dir / "scripts/not-a-tool.ps1", "ignored\n")
    write(repo_dir / "docs/not-a-tool.md", "ignored\n")
    backup_root = home_dir / ".ai-config-backup"
    for name in ("2026-01-01-010101000", "2026-01-02-010101000"):
        write(backup_root / name / ".ai-config-backup-owned", "ai-config-backup-v1\n")
    write(
        backup_root / "2026-01-03-010101000/.ai-config-backup-owned",
        "foreign\n",
    )
    write(
        backup_root / ".tmp-incomplete/.ai-config-backup-owned",
        "ai-config-backup-v1\n",
    )
    write(
        backup_root / "foreign/.ai-config-backup-owned",
        "ai-config-backup-v1\n",
    )

    result = run_script(repo_dir, home_dir, "list")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "claude (2 files)" in result.stdout
    assert "codex (1 files)" in result.stdout
    assert "agy (0 files)" in result.stdout
    assert "scripts (" not in result.stdout
    assert "docs (" not in result.stdout
    assert "Backups: 2 completed snapshots" in result.stdout


def test_project_requires_live_claude_before_backup_or_destination_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    write(home_dir / ".codex/config.toml", 'model = "live"\n')
    before_repo = snapshot_tree(repo_dir)
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "project", "codex")

    assert result.returncode != 0
    assert "not found" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(home_dir) == before_home


def test_project_preflights_live_claude_reparse_before_any_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-rules"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")
    write(external / "sensitive.md", "external sensitive\n")
    (home_dir / ".claude/rules").symlink_to(external, target_is_directory=True)
    write(home_dir / ".codex/config.toml", 'model = "live"\n')
    before_repo = snapshot_tree(repo_dir)
    before_home = snapshot_tree(home_dir)
    before_external = snapshot_tree(external)

    result = run_script(repo_dir, home_dir, "project", "codex")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(home_dir) == before_home
    assert snapshot_tree(external) == before_external
    assert not (home_dir / ".ai-config-backup").exists()


def test_apply_empty_projection_fails_without_creating_live_or_backup(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "empty repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode != 0
    assert "No files staged for claude" in result.stderr + result.stdout
    assert not (home_dir / ".claude").exists()
    assert not (home_dir / ".ai-config-backup").exists()


def test_codex_migrates_legacy_skills_once_after_backup(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "test"\n')
    write(
        repo_dir / "codex/skills/managed/SKILL.md",
        "---\nname: managed\ndescription: Managed\n---\nCurrent.\n",
    )
    legacy = home_dir / ".codex/skills"
    write(legacy / "hand-installed/SKILL.md", "hand installed\n")
    write(legacy / "hand-installed/auth.json", "legacy secret\n")

    first = run_script(repo_dir, home_dir, "apply", "codex")

    assert first.returncode == 0, first.stderr + first.stdout
    canonical = home_dir / ".agents/skills"
    assert (canonical / "hand-installed/SKILL.md").is_file()
    assert not (canonical / "hand-installed/auth.json").exists()
    assert (canonical / "managed/SKILL.md").is_file()
    assert (canonical / ".ai-config-codex-skills-migrated").is_file()
    snapshots = [
        path
        for path in (home_dir / ".ai-config-backup").iterdir()
        if path.is_dir()
    ]
    assert len(snapshots) == 1
    assert (
        snapshots[0] / "codex/skills/hand-installed/SKILL.md"
    ).read_text() == "hand installed\n"
    assert not (
        snapshots[0] / "codex/skills/hand-installed/auth.json"
    ).exists()

    shutil.rmtree(canonical / "hand-installed")
    second = run_script(repo_dir, home_dir, "apply", "codex")

    assert second.returncode == 0, second.stderr + second.stdout
    assert not (canonical / "hand-installed").exists()
    assert (legacy / "hand-installed/SKILL.md").is_file()


def test_codex_accepts_expected_legacy_skills_symlink(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "test"\n')
    write(
        repo_dir / "codex/skills/managed/SKILL.md",
        "---\nname: managed\ndescription: Managed\n---\nCurrent.\n",
    )
    canonical = home_dir / ".agents/skills"
    write(canonical / "hand-installed/SKILL.md", "hand installed\n")
    legacy = home_dir / ".codex/skills"
    legacy.parent.mkdir(parents=True)
    legacy.symlink_to(canonical, target_is_directory=True)

    result = run_script(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert legacy.is_symlink()
    assert legacy.resolve() == canonical.resolve()
    assert (canonical / "hand-installed/SKILL.md").is_file()
    assert (canonical / "managed/SKILL.md").is_file()


def test_codex_rejects_foreign_legacy_skills_symlink(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "managed"\n')
    write(external / "keep.txt", "external\n")
    legacy = home_dir / ".codex/skills"
    legacy.parent.mkdir(parents=True)
    legacy.symlink_to(external, target_is_directory=True)
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "apply", "codex")

    assert result.returncode != 0
    assert "legacy Codex skills target mismatch" in result.stderr + result.stdout
    assert snapshot_tree(home_dir) == before_home
    assert (external / "keep.txt").read_text() == "external\n"


def test_agy_accepts_expected_legacy_compatibility_symlink(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"test"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\nManaged.\n",
    )
    canonical = home_dir / ".gemini/config/skills"
    write(canonical / "hand-installed/SKILL.md", "hand installed\n")
    legacy = home_dir / ".gemini/antigravity/skills"
    legacy.parent.mkdir(parents=True)
    legacy.symlink_to(canonical, target_is_directory=True)

    result = run_script(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert legacy.is_symlink()
    assert legacy.resolve() == canonical.resolve()
    assert (canonical / "demo/SKILL.md").is_file()
    assert (
        home_dir / ".gemini/antigravity-cli/skills/demo/SKILL.md"
    ).is_file()


def test_agy_rejects_foreign_legacy_skills_symlink(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"managed"}\n')
    legacy = home_dir / ".gemini/antigravity/skills"
    legacy.parent.mkdir(parents=True)
    legacy.symlink_to(external, target_is_directory=True)
    write(external / "keep.txt", "external\n")
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "apply", "agy")

    assert result.returncode != 0
    assert "legacy Antigravity skills target mismatch" in (
        result.stderr + result.stdout
    )
    assert snapshot_tree(home_dir) == before_home
    assert (external / "keep.txt").read_text() == "external\n"


def test_agy_fallback_rejects_reparse_target_mismatch(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-skills"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"test"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\nManaged.\n",
    )

    first = run_script(repo_dir, home_dir, "apply", "agy")
    assert first.returncode == 0, first.stderr + first.stdout
    cli_skills = home_dir / ".gemini/antigravity-cli/skills"
    shutil.rmtree(cli_skills)
    write(external / "keep.txt", "external\n")
    cli_skills.symlink_to(external, target_is_directory=True)
    before_home = snapshot_tree(home_dir)

    second = run_script(repo_dir, home_dir, "apply", "agy")

    assert second.returncode != 0
    assert "reparse point" in (second.stderr + second.stdout).lower()
    assert (external / "keep.txt").read_text() == "external\n"
    assert snapshot_tree(home_dir) == before_home


def test_agy_fallback_rejects_reparse_cli_root_before_external_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-cli"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"managed"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\nManaged.\n",
    )
    write(external / "keep.txt", "external\n")
    before = {
        path.relative_to(external): path.read_bytes()
        for path in external.rglob("*")
        if path.is_file()
    }
    cli_root = home_dir / ".gemini/antigravity-cli"
    cli_root.parent.mkdir(parents=True)
    cli_root.symlink_to(external, target_is_directory=True)
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "apply", "agy")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    after = {
        path.relative_to(external): path.read_bytes()
        for path in external.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (external / ".ai-config-skills-state.json").exists()
    assert not (external / ".ai-config-skills-mirror").exists()
    assert not (external / "settings.json").exists()
    assert not (external / "skills").exists()
    assert snapshot_tree(home_dir) == before_home


def test_agy_fallback_rejects_reparse_marker_before_external_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-marker.txt"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"managed"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\nManaged.\n",
    )

    first = run_script(repo_dir, home_dir, "apply", "agy")
    assert first.returncode == 0, first.stderr + first.stdout
    marker = home_dir / ".gemini/antigravity-cli/.ai-config-skills-mirror"
    marker.unlink()
    write(external, "external marker\n")
    before = external.read_bytes()
    marker.symlink_to(external)
    write(home_dir / ".claude/CLAUDE.md", "instructions\n")
    before_home = snapshot_tree(home_dir)

    second = run_script(repo_dir, home_dir, "project", "agy")

    assert second.returncode != 0
    assert "reparse point" in (second.stderr + second.stdout).lower()
    assert external.read_bytes() == before
    assert snapshot_tree(home_dir) == before_home


def test_agy_fallback_preflights_reparse_state_before_any_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-state.json"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"managed"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\nManaged.\n",
    )
    first = run_script(repo_dir, home_dir, "apply", "agy")
    assert first.returncode == 0, first.stderr + first.stdout
    state = home_dir / ".gemini/antigravity-cli/.ai-config-skills-state.json"
    state.unlink()
    write(external, '{"sensitive":"external"}\n')
    state.symlink_to(external)
    before_home = snapshot_tree(home_dir)
    before_external = external.read_bytes()

    second = run_script(repo_dir, home_dir, "apply", "agy")

    assert second.returncode != 0
    assert "reparse point" in (second.stderr + second.stdout).lower()
    assert snapshot_tree(home_dir) == before_home
    assert external.read_bytes() == before_external


def test_apply_rejects_reparse_managed_directory_before_external_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-rules"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    write(repo_dir / "claude/rules/current.md", "new current\n")
    write(external / "current.md", "external current\n")
    write(external / "stale.md", "external stale\n")
    (home_dir / ".claude").mkdir()
    (home_dir / ".claude/rules").symlink_to(external, target_is_directory=True)

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert (external / "current.md").read_text() == "external current\n"
    assert (external / "stale.md").read_text() == "external stale\n"


def test_apply_rejects_reparse_managed_skill_before_external_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-skill"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "codex/config.toml", 'model = "safe"\n')
    write(
        repo_dir / "codex/skills/managed/SKILL.md",
        "---\nname: managed\ndescription: Managed\n---\nNew body.\n",
    )
    write(external / "SKILL.md", "external skill\n")
    write(external / "stale.txt", "external stale\n")
    skills = home_dir / ".agents/skills"
    skills.mkdir(parents=True)
    (skills / "managed").symlink_to(external, target_is_directory=True)
    write(skills / ".ai-config-managed", "managed\n")

    result = run_script(repo_dir, home_dir, "apply", "codex")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert (external / "SKILL.md").read_text() == "external skill\n"
    assert (external / "stale.txt").read_text() == "external stale\n"


def test_apply_rejects_reparse_top_level_file_before_external_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-instructions.md"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    write(external, "external instructions\n")
    (home_dir / ".claude").mkdir()
    (home_dir / ".claude/CLAUDE.md").symlink_to(external)

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert external.read_text() == "external instructions\n"


def test_apply_rejects_repo_top_level_file_reparse_before_any_home_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-instructions.md"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(external, "external sensitive instructions\n")
    instructions = repo_dir / "claude/CLAUDE.md"
    instructions.parent.mkdir(parents=True)
    instructions.symlink_to(external)
    write(home_dir / ".claude/CLAUDE.md", "live instructions must remain\n")
    before_home = snapshot_tree(home_dir)
    before_external = external.read_bytes()

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(home_dir) == before_home
    assert external.read_bytes() == before_external
    assert not (home_dir / ".ai-config-backup").exists()


@pytest.mark.parametrize("nested", [False, True], ids=["managed-root", "descendant"])
def test_apply_preflights_repo_managed_directory_reparse_tree(
    tmp_path: Path,
    nested: bool,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-rules"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    write(external / "sensitive.md", "external sensitive rule\n")
    rules = repo_dir / "claude/rules"
    if nested:
        write(rules / "local.md", "local rule\n")
        (rules / "external").symlink_to(external, target_is_directory=True)
    else:
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.symlink_to(external, target_is_directory=True)
    write(home_dir / ".claude/CLAUDE.md", "live instructions must remain\n")
    before_home = snapshot_tree(home_dir)
    before_external = snapshot_tree(external)

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(home_dir) == before_home
    assert snapshot_tree(external) == before_external
    assert not (home_dir / ".ai-config-backup").exists()


def test_backup_prune_preserves_foreign_and_incomplete_directories(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    write(home_dir / ".claude/CLAUDE.md", "old instructions\n")

    backup_root = home_dir / ".ai-config-backup"
    write(backup_root / "foreign-data/keep.txt", "foreign\n")
    write(backup_root / "2020-01-01-000000000/keep.txt", "no marker\n")
    write(backup_root / ".tmp-foreign/keep.txt", "incomplete\n")

    for _ in range(6):
        result = run_script(repo_dir, home_dir, "apply", "claude")
        assert result.returncode == 0, result.stderr + result.stdout

    assert (backup_root / "foreign-data/keep.txt").read_text() == "foreign\n"
    assert (
        backup_root / "2020-01-01-000000000/keep.txt"
    ).read_text() == "no marker\n"
    assert (backup_root / ".tmp-foreign/keep.txt").read_text() == "incomplete\n"
    completed = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and (path / ".ai-config-backup-owned").is_file()
    ]
    assert len(completed) == 5
    assert len({path.name for path in completed}) == 5


def test_apply_refuses_reparse_point_backup_root(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-backups"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    write(home_dir / ".claude/CLAUDE.md", "old instructions\n")
    backup_root = home_dir / ".ai-config-backup"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(external), str(backup_root))
    else:
        backup_root.symlink_to(external, target_is_directory=True)

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode != 0
    assert "reparse point backup root" in (result.stderr + result.stdout).lower()
    assert not list(external.iterdir())
    assert (home_dir / ".claude/CLAUDE.md").read_text() == "old instructions\n"


def test_failed_backup_cleans_only_its_own_temporary_directory(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    live_file = home_dir / ".claude/CLAUDE.md"
    write(live_file, "old instructions\n")
    backup_root = home_dir / ".ai-config-backup"
    write(backup_root / ".tmp-foreign/keep.txt", "foreign temp\n")
    script_path = repo_dir / "ai_config/backup.py"
    script = script_path.read_text(encoding="utf-8")
    anchor = "    temporary.mkdir()\n    try:\n"
    injected = anchor.replace(
        "    try:\n",
        "    try:\n        raise RuntimeError('Injected backup failure')\n",
    )
    assert script.count(anchor) == 1
    script_path.write_text(script.replace(anchor, injected, 1), encoding="utf-8")

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode != 0
    assert "Injected backup failure" in result.stderr + result.stdout
    assert (backup_root / ".tmp-foreign/keep.txt").read_text() == "foreign temp\n"
    leftovers = [
        path.name
        for path in backup_root.iterdir()
        if path.name != ".tmp-foreign" and path.name != ".ai-config-backup.lock"
    ]
    assert leftovers == []


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Junction contract")
def test_python_migrates_owned_legacy_windows_junction(tmp_path: Path) -> None:
    import _winapi

    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"test"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\n",
    )
    legacy = home_dir / ".gemini/antigravity/skills"
    write(legacy / "hand-installed/SKILL.md", "hand installed\n")
    cli = home_dir / ".gemini/antigravity-cli"
    cli.mkdir(parents=True)
    _winapi.CreateJunction(str(legacy), str(cli / "skills"))
    write_skills_ownership(cli, legacy, "junction")

    result = run_script(
        repo_dir,
        home_dir,
        "apply",
        "agy",
        force_copy_fallback=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    canonical = home_dir / ".gemini/config/skills"
    assert os.path.samefile(cli / "skills", canonical)
    assert (canonical / "hand-installed/SKILL.md").is_file()
    assert (canonical / "demo/SKILL.md").is_file()
    current_state = json.loads(
        (cli / ".ai-config-skills-state.json").read_text(encoding="utf-8")
    )
    assert os.path.samefile(current_state["entries"][0]["source"], canonical)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Junction contract")
def test_python_creates_native_windows_junctions(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "agy/settings.json", '{"theme":"test"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\n",
    )
    result = run_script(
        repo_dir,
        home_dir,
        "apply",
        "all",
        force_copy_fallback=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    agy_skills = home_dir / ".gemini/antigravity-cli/skills"
    reparse_flag = stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert agy_skills.lstat().st_file_attributes & reparse_flag
    agy_state = json.loads(
        (
            home_dir
            / ".gemini/antigravity-cli/.ai-config-skills-state.json"
        ).read_text()
    )
    assert agy_state["entries"][0]["kind"] == "junction"
