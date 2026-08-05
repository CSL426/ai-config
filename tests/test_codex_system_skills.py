"""The legacy Codex skills migration must leave Codex's own .system alone."""

from pathlib import Path

from test_apply_projection import write


def test_merge_missing_tree_skips_excluded_dir_names(tmp_path: Path) -> None:
    from ai_config.fsops import merge_missing_tree

    legacy = tmp_path / "legacy"
    canonical = tmp_path / "canonical"
    write(legacy / "mine/SKILL.md", "user skill\n")
    # Codex installs and version-checks this itself; migrating it strands a
    # stale copy where Codex never looks.
    write(legacy / ".system/imagegen/SKILL.md", "vendor skill\n")

    merge_missing_tree(
        legacy, canonical, "legacy Codex skills", exclude_dir_names=frozenset({".system"})
    )

    assert (canonical / "mine/SKILL.md").is_file()
    assert not (canonical / ".system").exists()


def test_merge_missing_tree_still_copies_everything_by_default(tmp_path: Path) -> None:
    from ai_config.fsops import merge_missing_tree

    legacy = tmp_path / "legacy"
    canonical = tmp_path / "canonical"
    write(legacy / "mine/SKILL.md", "user skill\n")
    write(legacy / ".system/imagegen/SKILL.md", "vendor skill\n")

    merge_missing_tree(legacy, canonical, "legacy")

    assert (canonical / "mine/SKILL.md").is_file()
    assert (canonical / ".system/imagegen/SKILL.md").is_file()


def test_codex_migration_leaves_system_behind(tmp_path: Path, monkeypatch) -> None:
    from ai_config import paths
    from ai_config.tools import codex

    legacy = tmp_path / ".codex" / "skills"
    canonical = tmp_path / ".agents" / "skills"
    write(legacy / "mine/SKILL.md", "user skill\n")
    write(legacy / ".system/imagegen/SKILL.md", "vendor skill\n")

    monkeypatch.setattr(codex, "CODEX_LEGACY_SKILLS", legacy)
    monkeypatch.setattr(codex, "CODEX_CANONICAL_SKILLS", canonical)
    monkeypatch.setattr(paths, "CODEX_LEGACY_SKILLS", legacy, raising=False)

    codex.prepare_codex_canonical_skills()

    assert (canonical / "mine/SKILL.md").is_file()
    assert not (canonical / ".system").exists()
    # The vendor copy stays where Codex manages it.
    assert (legacy / ".system/imagegen/SKILL.md").is_file()
