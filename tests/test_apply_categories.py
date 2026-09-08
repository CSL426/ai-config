"""Selected categories isolate writes, safety checks, and memory enablement."""

import json
import os

import pytest
from test_apply_projection import copy_runtime_files, run_ai_config, write

from ai_config.instructionblocks import preserve_memory_block

BLOCK = "<!-- acg:memory:begin -->\nlocal memory\n<!-- acg:memory:end -->"


@pytest.fixture
def checkout(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    copy_runtime_files(repo)
    write(repo / "claude/CLAUDE.md", "new rules\n")
    return repo, home


@pytest.mark.parametrize("tool", ["claude", "codex", "agy"])
def test_settings_preserves_every_skill_surface(checkout, tool):
    repo, home = checkout
    write(repo / "agy/settings.json", '{"theme":"new"}\n')
    write(repo / "claude/agents/example.md", "agent content\n")
    surfaces = [
        ".claude/skills", ".codex/skills", ".agents/skills",
        ".gemini/antigravity/skills", ".gemini/config/skills",
        ".gemini/antigravity-cli/skills",
    ]
    for surface in surfaces:
        write(home / surface / "keep/SKILL.md", "old skill\n")
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = run_ai_config(repo, home, "apply", tool, "--category", "settings")
    assert result.returncode == 0, result.stdout + result.stderr
    for relative, content in before.items():
        assert (home / relative).read_bytes() == content
    for surface in surfaces:
        assert sorted(p.name for p in (home / surface).iterdir()) == ["keep"]


@pytest.mark.parametrize("tool, surface", [
    ("claude", ".claude/skills"),
    ("codex", ".agents/skills"),
    ("agy", ".gemini/config/skills"),
])
def test_skills_ignores_invalid_settings_and_memory(checkout, tool, surface):
    repo, home = checkout
    write(repo / "claude/settings.json", "{invalid")
    write(repo / "agy/settings.json", "{invalid")
    write(repo / "claude/CLAUDE.md", "<!-- acg:memory:begin -->")
    write(repo / "claude/skills/example/SKILL.md", "---\nname: example\n---\nSkill\n")
    write(home / ".claude/CLAUDE.md", "<!-- acg:memory:end -->")
    result = run_ai_config(repo, home, "apply", "--category", "skills", tool)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / surface / "example/SKILL.md").is_file()
    assert (home / ".claude/CLAUDE.md").read_text() == "<!-- acg:memory:end -->"
    assert not (home / ".claude/settings.json").exists()


@pytest.mark.parametrize("tool,surface", [
    ("codex", ".agents/skills"), ("agy", ".gemini/config/skills"),
])
def test_empty_selected_skills_prunes_with_backup(checkout, tool, surface):
    repo, home = checkout
    write(home / surface / "old/SKILL.md", "old\n")
    write(home / surface / "manual/SKILL.md", "manual\n")
    write(home / surface / ".ai-config-managed", "old\n")
    if tool == "agy" and os.name != "nt":
        cli = home / ".gemini/antigravity-cli/skills"
        cli.parent.mkdir(parents=True)
        cli.symlink_to(home / surface, target_is_directory=True)
    result = run_ai_config(repo, home, "apply", tool, "--category", "skills")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (home / surface / "old").exists()
    assert (home / surface / "manual/SKILL.md").is_file()
    assert list((home / ".ai-config-backup").glob(f"*/{tool}/skills/old/SKILL.md"))


@pytest.mark.parametrize("args", [
    ("--category", "invalid"), ("--category",),
    ("--category", "skills", "--category", "settings"),
    ("claude", "codex", "--category", "all"),
])
def test_invalid_arguments_do_not_create_live_or_backup(checkout, args):
    repo, home = checkout
    result = run_ai_config(repo, home, "apply", *args)
    assert result.returncode != 0
    assert not list(home.iterdir())


@pytest.mark.parametrize("local_enabled", [False, True])
def test_settings_keeps_local_memory_choice(checkout, local_enabled):
    repo, home = checkout
    write(repo / "claude/CLAUDE.md", "new rules\n" + BLOCK.replace("local", "repo") + "\n")
    write(home / ".claude/CLAUDE.md", "old rules\n" + (BLOCK if local_enabled else ""))
    result = run_ai_config(repo, home, "apply", "claude", "--category", "settings")
    assert result.returncode == 0, result.stdout + result.stderr
    actual = (home / ".claude/CLAUDE.md").read_text()
    assert "new rules" in actual
    assert (BLOCK in actual) == local_enabled
    assert "repo memory" not in actual


@pytest.mark.parametrize("broken", [
    "<!-- acg:memory:begin -->", "<!-- acg:memory:end -->", BLOCK + BLOCK,
    "<!-- acg:memory:end -->\n<!-- acg:memory:begin -->",
])
def test_malformed_memory_aborts_before_any_tool_write(checkout, broken):
    repo, home = checkout
    write(home / ".codex/AGENTS.md", broken)
    result = run_ai_config(repo, home, "apply", "all", "--category", "settings")
    assert result.returncode != 0
    assert not (home / ".claude").exists()
    assert not (home / ".ai-config-backup").exists()


def test_memory_merge_preserves_unrelated_raw_bytes():
    block = BLOCK.encode()
    source = b"\xef\xbb\xbfnew\r\n" + block + b"\r\nafter\r\n"
    live_block = block.replace(b"local memory", b"local\r\nmemory")
    assert preserve_memory_block(source, live_block) == (
        b"\xef\xbb\xbfnew\r\n" + live_block + b"\r\nafter\r\n"
    )


def test_settings_backup_excludes_skills(checkout):
    repo, home = checkout
    write(home / ".codex/AGENTS.md", "old rules\n")
    write(home / ".agents/skills/secret/SKILL.md", "skill content\n")
    result = run_ai_config(repo, home, "apply", "codex", "--category", "settings")
    assert result.returncode == 0, result.stdout + result.stderr
    manifests = list((home / ".ai-config-backup").glob("*/destinations.json"))
    assert manifests
    assert all("skills" not in item["destination"] for item in json.loads(manifests[0].read_text()))


@pytest.mark.parametrize("tool", ["codex", "agy"])
def test_settings_does_not_create_skill_roots(checkout, tool):
    repo, home = checkout
    write(repo / "agy/settings.json", '{"theme":"new"}\n')
    result = run_ai_config(repo, home, "apply", tool, "--category", "settings")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (home / ".agents").exists()
    assert not (home / ".gemini/config").exists()
    assert not (home / ".codex/skills").exists()
    assert not (home / ".gemini/antigravity-cli/skills").exists()


@pytest.mark.skipif(os.name == "nt", reason="Unix leaf link isolation")
def test_unselected_unsafe_leaf_does_not_block_selected_category(checkout):
    repo, home = checkout
    write(home / ".claude/skills/keep/SKILL.md", "keep\n")
    (home / ".claude/settings.json").symlink_to(home / "outside.json")
    write(repo / "claude/skills/example/SKILL.md", "Skill\n")
    result = run_ai_config(repo, home, "apply", "claude", "--category", "skills")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".claude/settings.json").is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="Unix skill link surface")
def test_empty_agy_source_prunes_canonical_without_prior_cli_link(checkout):
    repo, home = checkout
    canonical = home / ".gemini/config/skills"
    write(canonical / "old/SKILL.md", "old\n")
    write(canonical / ".ai-config-managed", "old\n")
    result = run_ai_config(repo, home, "apply", "agy", "--category", "skills")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (canonical / "old").exists()
    assert (home / ".gemini/antigravity-cli/skills").is_symlink()
