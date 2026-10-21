"""Skills acg wrote to a tool's old folder go once they are migrated.

DESKTOP-TLG88N2, 2026-10-21: Codex read both ~/.codex/skills and
~/.agents/skills and loaded eleven skills twice; the old copies were July's.
"""

import os
import sys
from pathlib import Path

import pytest
from test_apply_projection import copy_runtime_files, run_ai_config, write

from ai_config.paths import MANIFEST_NAME
from ai_config.skills import legacy_managed_skills, retire_legacy_skills


def _legacy(root: Path, managed: "list[str]") -> Path:
    for name in managed:
        write(root / name / "SKILL.md", f"---\nname: {name}\n---\nold copy\n")
    write(root / MANIFEST_NAME, "".join(f"{name}\n" for name in managed))
    return root


def test_only_skills_in_the_old_manifest_go(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path / "skills", ["commit", "design-md"])
    write(legacy / "hand-installed/SKILL.md", "mine\n")
    write(legacy / ".system/imagegen/SKILL.md", "vendor\n")

    assert retire_legacy_skills(legacy, "Codex") == ["commit", "design-md"]

    assert sorted(entry.name for entry in legacy.iterdir()) == [".system", "hand-installed"]
    # 第二次沒有東西可收,也不會出錯
    assert retire_legacy_skills(legacy, "Codex") == []


def test_a_manifest_name_that_escapes_the_folder_is_ignored(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write(outside / "SKILL.md", "keep\n")
    legacy = tmp_path / "skills"
    write(legacy / MANIFEST_NAME, "../outside\n")

    assert legacy_managed_skills(legacy) == []
    retire_legacy_skills(legacy, "Codex")
    assert (outside / "SKILL.md").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix symlink")
def test_a_legacy_folder_that_links_elsewhere_is_left_alone(tmp_path: Path) -> None:
    canonical = _legacy(tmp_path / "canonical", ["commit"])
    legacy = tmp_path / "legacy"
    os.symlink(canonical, legacy, target_is_directory=True)

    assert retire_legacy_skills(legacy, "Codex") == []
    assert (canonical / "commit/SKILL.md").is_file()
    assert (canonical / MANIFEST_NAME).is_file()


def test_a_dot_name_in_the_old_manifest_is_never_removed(tmp_path: Path) -> None:
    """An older acg once migrated Codex's .system; a stale manifest could list it."""
    legacy = _legacy(tmp_path / "skills", [".system"])

    assert retire_legacy_skills(legacy, "Codex") == []
    assert (legacy / ".system/SKILL.md").is_file()


def test_apply_migrates_then_retires_and_keeps_a_backup(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "x"\n')
    write(repo_dir / "agy/settings.json", '{"theme":"neon"}\n')
    codex_legacy = _legacy(home_dir / ".codex/skills", ["commit", "design-md"])
    write(codex_legacy / ".system/imagegen/SKILL.md", "vendor\n")
    agy_legacy = _legacy(home_dir / ".gemini/antigravity/skills", ["commit"])

    before = run_ai_config(repo_dir, home_dir, "status", "all")
    assert "old skill copies acg wrote" in before.stdout, before.stdout + before.stderr

    for tool in ("codex", "agy"):
        result = run_ai_config(repo_dir, home_dir, "apply", tool)
        assert result.returncode == 0, result.stderr + result.stdout
        assert "Removed" in result.stdout
    assert sorted(entry.name for entry in codex_legacy.iterdir()) == [".system"]
    assert list(agy_legacy.iterdir()) == []
    backups = list((home_dir / ".ai-config-backup").glob("*/codex/legacy-skills/commit/SKILL.md"))
    assert backups, "the apply snapshot keeps the old copies"
    assert list((home_dir / ".ai-config-backup").glob("*/agy/legacy-skills/commit/SKILL.md"))

    after = run_ai_config(repo_dir, home_dir, "status", "all")
    assert "old skill copies acg wrote" not in after.stdout
