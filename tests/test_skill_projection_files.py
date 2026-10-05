"""A skill reaches Codex and agy whole, not just its four best-known folders."""

from pathlib import Path

import pytest
from test_apply_projection import copy_runtime_files, run_ai_config, write

from ai_config.skills import sync_skills

# 只挑 examples/references/scripts/agents 時,這些全部在 Codex 和 agy 不見:
# ui-ux-pro-max 的 data/ 資料庫、範本、graphify 根目錄的版本檔
CARRIED = (
    "templates/report.md",
    "workflows/release.md",
    "data/styles.csv",
    "assets/logo.svg",
    "references/guide.md",
    "scripts/run.py",
    "LICENSE.txt",
    ".tool_version",
)


def _skill(root: Path) -> None:
    write(root / "demo/SKILL.md", "---\nname: demo\ndescription: Demo skill\n---\nBody.\n")
    for rel in CARRIED:
        write(root / "demo" / rel, f"{rel}\n")
    write(root / "demo/data/auth.json", '{"token": "x"}\n')


def test_apply_carries_every_file_of_a_skill_to_codex_and_agy(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    _skill(repo_dir / "claude/skills")
    write(repo_dir / "codex/config.toml", 'personality = "gpt-5"\n')
    write(repo_dir / "agy/settings.json", '{"theme":"neon"}\n')

    for tool in ("codex", "agy"):
        result = run_ai_config(repo_dir, home_dir, "apply", tool)
        assert result.returncode == 0, result.stderr + result.stdout

    for live in (home_dir / ".agents/skills/demo", home_dir / ".gemini/config/skills/demo"):
        for rel in CARRIED:
            assert (live / rel).read_text(encoding="utf-8") == f"{rel}\n", (live, rel)
        assert (live / "SKILL.md").is_file()
        # 憑證檔照舊不帶過去
        assert not (live / "data/auth.json").exists()

    # 帶過去的檔案不算漂移,下一次 apply 也不會刪
    status = run_ai_config(repo_dir, home_dir, "status", "codex")
    assert "only in live" not in status.stdout, status.stdout
    again = run_ai_config(repo_dir, home_dir, "apply", "codex")
    assert again.returncode == 0, again.stderr + again.stdout
    assert (home_dir / ".agents/skills/demo/data/styles.csv").is_file()


def test_a_symlink_at_the_top_of_a_skill_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "src"
    write(src / "demo/SKILL.md", "---\nname: demo\ndescription: d\n---\n")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    try:
        (src / "demo/notes.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(RuntimeError, match="symlink"):
        sync_skills(src, tmp_path / "dst")
    assert not (tmp_path / "dst/demo/notes.txt").exists()


def test_apply_names_files_added_by_hand_before_removing_them(tmp_path: Path) -> None:
    # 同事把缺的資料庫 rsync 進 ~/.agents/skills,下一次 apply 就默默不見
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/skills/demo/SKILL.md", "---\nname: demo\ndescription: d\n---\n")
    write(repo_dir / "codex/config.toml", 'personality = "gpt-5"\n')
    first = run_ai_config(repo_dir, home_dir, "apply", "codex")
    assert first.returncode == 0, first.stderr + first.stdout
    assert "直接加進" not in first.stdout

    live = home_dir / ".agents/skills/demo"
    write(live / "data/patched.csv", "by hand\n")

    second = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert second.returncode == 0, second.stderr + second.stdout
    assert "直接加進" in second.stdout
    assert "demo/data/patched.csv" in second.stdout
    assert "~/.claude/skills/" in second.stdout
    assert not (live / "data/patched.csv").exists()
    backups = list((home_dir / ".ai-config-backup").rglob("patched.csv"))
    assert backups, "apply should back the file up before removing it"
