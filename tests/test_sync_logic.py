"""Tests for the riskier sync internals: frontmatter sanitizing, managed-skill
orphan pruning (destructive path), shared-mirror drift detection, and the
commands/ projection."""

import hashlib
from pathlib import Path

from test_apply_projection import IMPL, copy_runtime_files, run_ai_config, write


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    # Minimal codex config so apply codex has something to stage
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    return repo_dir, home_dir


# ─── sanitize_skill_frontmatter ───────────────────────────────


def test_agent_without_frontmatter_gets_synthesized_frontmatter(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/agents/bare-agent.md", "# Bare Agent\n\nDoes things.\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    skill = (home_dir / ".codex/skills/bare-agent/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    expected_name = "name: 'Bare Agent'\n" if IMPL == "py" else "name: Bare Agent\n"
    assert expected_name in skill
    assert "description: >-\n" in skill
    assert "short-description: " in skill
    assert "Does things.\n" in skill


def test_description_with_colon_is_rewritten_as_block_scalar(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/agents/colon-agent.md",
        "---\nname: colon-agent\ndescription: Use when: things break\n---\nBody.\n",
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    skill = (home_dir / ".codex/skills/colon-agent/SKILL.md").read_text(encoding="utf-8")
    assert "description: >-\n  Use when: things break\n" in skill
    assert "short-description: " in skill


def test_existing_metadata_block_is_preserved(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/shared/both/mirrored/SKILL.md",
        "---\n"
        "name: mirrored\n"
        "description: A mirrored skill.\n"
        "metadata:\n"
        "  mirror-of: ~/source.md\n"
        "  mirror-hash: abc\n"
        "---\nBody.\n",
    )
    write(home_dir / "source.md", "anything\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    skill = (home_dir / ".codex/skills/mirrored/SKILL.md").read_text(encoding="utf-8")
    assert "mirror-of: ~/source.md\n" in skill
    assert "mirror-hash: abc\n" in skill


# ─── reconcile_managed_skills (orphan pruning) ────────────────


def test_removed_shared_skill_is_pruned_but_hand_installed_survives(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    shared_skill = repo_dir / "claude/shared/both/skill-a/SKILL.md"
    write(shared_skill, "---\nname: skill-a\ndescription: A.\n---\nA body.\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")
    assert result.returncode == 0, result.stderr + result.stdout
    assert (home_dir / ".codex/skills/skill-a/SKILL.md").is_file()
    manifest = (home_dir / ".codex/skills/.ai-config-managed").read_text(encoding="utf-8")
    assert "skill-a" in manifest

    # Hand-installed skill we never managed
    write(
        home_dir / ".codex/skills/manual-skill/SKILL.md",
        "---\nname: manual-skill\n---\nManual.\n",
    )

    # Remove the source; next apply must prune skill-a only
    shared_skill.unlink()
    shared_skill.parent.rmdir()

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")
    assert result.returncode == 0, result.stderr + result.stdout
    assert not (home_dir / ".codex/skills/skill-a").exists()
    assert (home_dir / ".codex/skills/manual-skill/SKILL.md").is_file()
    manifest = (home_dir / ".codex/skills/.ai-config-managed").read_text(encoding="utf-8")
    assert "skill-a" not in manifest
    assert "manual-skill" not in manifest


# ─── check_shared_mirrors (drift detection) ───────────────────


def mirror_skill(source_rel: str, source_hash: str) -> str:
    return (
        "---\n"
        "name: mirrored\n"
        "description: A mirrored skill.\n"
        "metadata:\n"
        f"  mirror-of: ~/{source_rel}\n"
        f"  mirror-hash: {source_hash}\n"
        "---\nBody.\n"
    )


def test_status_reports_mirror_up_to_date(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    source_content = "the source\n"
    source = home_dir / "source.md"
    write(source, source_content)
    write(
        repo_dir / "claude/shared/both/mirrored/SKILL.md",
        mirror_skill("source.md", hashlib.sha256(source.read_bytes()).hexdigest()),
    )

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "All 1 mirrored shared skills up to date" in result.stdout
    assert "mirror stale" not in result.stdout


def test_status_warns_when_mirror_source_changed(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    source = home_dir / "source.md"
    write(source, "the source\n")
    write(
        repo_dir / "claude/shared/both/mirrored/SKILL.md",
        mirror_skill("source.md", sha256("an older version\n")),
    )

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "mirror stale" in result.stdout
    assert "both/mirrored/SKILL.md" in result.stdout
    # Suggests the hash to set after refreshing the copy
    assert hashlib.sha256(source.read_bytes()).hexdigest() in result.stdout


def test_status_warns_when_mirror_source_missing(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/shared/both/mirrored/SKILL.md",
        mirror_skill("gone.md", sha256("whatever\n")),
    )

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "mirror source missing" in result.stdout


def test_unmirrored_shared_skill_is_ignored_by_drift_check(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/shared/both/plain/SKILL.md",
        "---\nname: plain\ndescription: No mirror.\n---\nBody.\n",
    )

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "mirror stale" not in result.stdout
    assert "mirror source missing" not in result.stdout
    assert "mirrored shared skills up to date" not in result.stdout


# ─── commands/ projection ─────────────────────────────────────


def test_commands_dir_is_applied_to_claude_home(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "claude/commands/commit.md", "---\ndescription: x\n---\nbody\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (
        home_dir / ".claude/commands/commit.md"
    ).read_text(encoding="utf-8") == "---\ndescription: x\n---\nbody\n"


# ─── plugin drift detection ───────────────────────────────────


def test_status_warns_when_agy_has_plugin_claude_dropped(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/settings.json",
        '{"enabledPlugins": {"keep@mp": true, "disabled@mp": false}}\n',
    )
    write(
        home_dir / ".gemini/antigravity-cli/plugins/installed_plugins.json",
        '{"version": 1, "plugins": {'
        '"keep@mp": [{"version": "1.0.0"}], '
        '"disabled@mp": [{"version": "1.0.0"}], '
        '"ghost@mp": [{"version": "5.1.0"}]}}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "status")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "agy has plugin not tracked in claude/settings.json: ghost@mp" in result.stdout
    assert "keep@mp" not in result.stdout.replace("ghost@mp", "")
    assert "disabled@mp\n" not in result.stdout


def test_status_warns_when_live_codex_has_plugin_repo_dropped(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/settings.json", '{"enabledPlugins": {"keep@mp": true}}\n')
    write(
        home_dir / ".codex/config.toml",
        'model = "gpt-5"\n\n[plugins."lingering@openai-curated"]\nenabled = true\n',
    )

    result = run_ai_config(repo_dir, home_dir, "status")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (
        "codex live config has plugin not in repo codex/config.toml: lingering@openai-curated"
        in result.stdout
    )


def test_status_reports_no_plugin_drift_when_aligned(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/settings.json", '{"enabledPlugins": {"keep@mp": true}}\n')
    write(
        home_dir / ".gemini/antigravity-cli/plugins/installed_plugins.json",
        '{"version": 1, "plugins": {"keep@mp": [{"version": "1.0.0"}]}}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "status")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No plugin drift detected" in result.stdout


def test_quoted_description_yields_valid_short_description(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "claude/agents/quoted-agent.md",
        '---\nname: quoted-agent\n'
        'description: "Anti-slop skill for pages. Use when building."\n'
        "---\nBody.\n",
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    skill = (home_dir / ".codex/skills/quoted-agent/SKILL.md").read_text(encoding="utf-8")
    assert "  short-description: 'Anti-slop skill for pages'\n" in skill
