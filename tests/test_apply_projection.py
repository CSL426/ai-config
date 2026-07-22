import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def copy_runtime_files(repo_dir: Path) -> None:
    shutil.copytree(REPO_ROOT / "ai_config", repo_dir / "ai_config")


def run_ai_config(
    repo_dir: Path,
    home_dir: Path,
    *args: str,
    input_text: "str | None" = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    cmd = [sys.executable, "-m", "ai_config", *args]
    return subprocess.run(
        cmd,
        cwd=repo_dir,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def test_apply_all_projects_claude_shared_content_without_sync(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "shared instructions\n")
    write(repo_dir / "claude/mcp.json", '{"mcpServers":{"demo":{"command":"demo"}}}\n')
    write(
        repo_dir / "claude/settings.json",
        '{"extraKnownMarketplaces":{"demo":{"source":{"repo":"acme/demo"}}}}\n',
    )
    write(
        repo_dir / "claude/agents/shared-agent.md",
        "---\nname: shared-agent\ndescription: Shared agent\n---\nShared agent body.\n",
    )
    write(repo_dir / "claude/rules/common/shared.md", "shared rule\n")

    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    write(repo_dir / "codex/rules/custom/private.md", "private codex rule\n")
    write(
        repo_dir / "codex/skills/private-skill/SKILL.md",
        "---\nname: private-skill\n---\nPrivate codex skill\n",
    )
    write(repo_dir / "agy/settings.json", '{"theme":"neon"}\n')

    write(
        home_dir / ".codex/config.toml",
        '[projects."/tmp/demo"]\ntrust_level = "trusted"\n',
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "all")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (home_dir / ".claude/CLAUDE.md").read_text(encoding="utf-8") == "shared instructions\n"
    assert (home_dir / ".codex/AGENTS.md").read_text(encoding="utf-8") == "shared instructions\n"
    assert (home_dir / ".gemini/antigravity-cli/mcp_config.json").read_text(encoding="utf-8") == (
        '{"mcpServers":{"demo":{"command":"demo"}}}\n'
    )
    assert (home_dir / ".gemini/antigravity-cli/settings.json").read_text(encoding="utf-8") == (
        '{"theme":"neon"}\n'
    )
    assert (
        home_dir / ".agents/skills/shared-agent/SKILL.md"
    ).read_text(encoding="utf-8").endswith("Shared agent body.\n")
    assert (
        home_dir / ".gemini/antigravity-cli/skills/shared-agent/SKILL.md"
    ).read_text(encoding="utf-8").endswith("Shared agent body.\n")
    assert (home_dir / ".codex/rules/common/shared.md").read_text(encoding="utf-8") == "shared rule\n"
    assert (
        home_dir / ".codex/rules/custom/private.md"
    ).read_text(encoding="utf-8") == "private codex rule\n"
    # Private skills pass through sanitize_skill_frontmatter, which synthesizes
    # description + metadata.short-description for strict parsers.
    private_skill = (home_dir / ".agents/skills/private-skill/SKILL.md").read_text(encoding="utf-8")
    assert "name: private-skill\n" in private_skill
    assert "short-description: " in private_skill
    assert private_skill.endswith("Private codex skill\n")

    codex_config = (home_dir / ".codex/config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5"' in codex_config
    assert '[projects."/tmp/demo"]' in codex_config
    assert 'trust_level = "trusted"' in codex_config


@pytest.mark.skipif(
    os.name == "nt", reason="Exercises the Unix symlink migration path only"
)
def test_apply_agy_migrates_expected_legacy_unix_link(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"neon"}\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo\n---\nManaged.\n",
    )
    legacy = home_dir / ".gemini/antigravity/skills"
    write(legacy / "hand-installed/SKILL.md", "hand installed\n")
    cli_skills = home_dir / ".gemini/antigravity-cli/skills"
    cli_skills.parent.mkdir(parents=True)
    cli_skills.symlink_to(legacy, target_is_directory=True)

    result = run_ai_config(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    canonical = home_dir / ".gemini/config/skills"
    assert cli_skills.is_symlink()
    assert cli_skills.resolve() == canonical.resolve()
    assert (canonical / "hand-installed/SKILL.md").is_file()
    assert (canonical / "demo/SKILL.md").is_file()
