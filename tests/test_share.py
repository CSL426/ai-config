"""Behaviour tests for `ai-config share` — copying a Claude-side skill
(plain ~/.claude/skills/ or an installed plugin's marketplace copy) into
the repo's claude/shared/ projection area."""

from pathlib import Path

from test_apply_projection import copy_runtime_files, run_ai_config, write


def make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    (repo_dir / "claude").mkdir(exist_ok=True)
    return repo_dir, home_dir


def test_share_copies_plain_claude_skill(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(home_dir / ".claude/skills/wiki/SKILL.md", "# wiki\n")
    write(home_dir / ".claude/skills/wiki/references/api.md", "api\n")
    write(home_dir / ".claude/skills/wiki/notes.txt", "not synced\n")

    result = run_ai_config(repo_dir, home_dir, "share", "wiki")

    assert result.returncode == 0, result.stderr + result.stdout
    dest = repo_dir / "claude/shared/both/wiki"
    assert (dest / "SKILL.md").read_text() == "# wiki\n"
    assert (dest / "references/api.md").is_file()
    # 與 shared 同步規則一致:SKILL.md/examples/references/scripts/agents 以外不搬
    assert not (dest / "notes.txt").exists()


def test_share_finds_plugin_marketplace_skill(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        home_dir / ".claude/plugins/marketplaces/community/eli5/skills/eli5/SKILL.md",
        "# eli5\n",
    )

    result = run_ai_config(repo_dir, home_dir, "share", "eli5")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (repo_dir / "claude/shared/both/eli5/SKILL.md").read_text() == "# eli5\n"


def test_share_to_target_and_replaces_existing(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(home_dir / ".claude/skills/wiki/SKILL.md", "# v2\n")
    write(repo_dir / "claude/shared/codex/wiki/SKILL.md", "# v1\n")
    write(repo_dir / "claude/shared/codex/wiki/stale.md", "old\n")

    result = run_ai_config(repo_dir, home_dir, "share", "wiki", "--to", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    dest = repo_dir / "claude/shared/codex/wiki"
    assert (dest / "SKILL.md").read_text() == "# v2\n"
    assert not (dest / "stale.md").exists()
    assert not (repo_dir / "claude/shared/both/wiki").exists()


def test_share_rejects_unknown_skill_target_and_usage(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(home_dir / ".claude/skills/wiki/SKILL.md", "# wiki\n")

    assert run_ai_config(repo_dir, home_dir, "share", "nope").returncode == 1
    assert (
        run_ai_config(
            repo_dir, home_dir, "share", "wiki", "--to", "vim"
        ).returncode
        == 1
    )
    assert run_ai_config(repo_dir, home_dir, "share").returncode == 1
