"""Windows apply: projecting configuration and managed skills to each tool."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from windows_test_helpers import (
    copy_runtime_files,
    make_env,
    run_script,
    snapshot_tree,
    write,
    write_skills_ownership,
)


def load_frontmatter(content: str) -> dict[str, object]:
    assert content.startswith("---\n")
    frontmatter, separator, _ = content[4:].partition("\n---\n")
    assert separator
    parsed = yaml.safe_load(frontmatter)
    assert isinstance(parsed, dict)
    return parsed


def test_antigravity_alias_applies_agy_configuration(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"neon"}\n')

    result = run_script(repo_dir, home_dir, "apply", "antigravity")

    assert result.returncode == 0, result.stderr + result.stdout
    settings = home_dir / ".gemini/antigravity-cli/settings.json"
    assert settings.read_text(encoding="utf-8") == '{"theme":"neon"}\n'


def test_init_codex_collects_only_filtered_general_config(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    live_config = home_dir / ".codex/config.toml"
    live_config.parent.mkdir(parents=True)
    live_config.write_bytes(
        b"\xef\xbb\xbf"
        b'personality = "live"\n\n'
        b'[projects."C:/one"]\ntrust_level = "trusted"\n\n'
        b"[features]\nsearch = true\n\n"
        b'[projects."C:/two"]\ntrust_level = "untrusted"\n\n'
        b"[notice]\nhide = false\n\n"
    )
    write(home_dir / ".codex/AGENTS.md", "live agents\n")
    write(home_dir / ".codex/rules/live.md", "live rule\n")
    write(home_dir / ".agents/skills/live/SKILL.md", "live skill\n")
    write(repo_dir / "codex/AGENTS.md", "repo agents\n")
    write(repo_dir / "codex/rules/repo.md", "repo rule\n")
    write(repo_dir / "codex/skills/repo/SKILL.md", "repo skill\n")

    result = run_script(repo_dir, home_dir, "init", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (repo_dir / "codex/config.toml").read_bytes() == (
        b'personality = "live"\n\n'
        b"[features]\nsearch = true\n\n"
        b"[notice]\nhide = false\n"
    )
    assert (repo_dir / "codex/AGENTS.md").read_text() == "repo agents\n"
    assert (repo_dir / "codex/rules/repo.md").read_text() == "repo rule\n"
    assert (repo_dir / "codex/skills/repo/SKILL.md").read_text() == "repo skill\n"
    assert not (repo_dir / "codex/rules/live.md").exists()
    assert not (repo_dir / "codex/skills/live").exists()


def test_init_codex_requires_live_directory_without_mutating_repo(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    before = snapshot_tree(repo_dir)

    result = run_script(repo_dir, home_dir, "init", "codex")

    assert result.returncode != 0
    assert "not found" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before


def test_init_agy_alias_warns_when_missing_and_collects_only_settings(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"repo"}\n')
    write(repo_dir / "agy/mcp_config.json", '{"repo":true}\n')
    write(repo_dir / "agy/skills/repo/SKILL.md", "repo skill\n")

    missing = run_script(repo_dir, home_dir, "init", "antigravity-cli")
    assert missing.returncode == 0, missing.stderr + missing.stdout
    assert "not found" in (missing.stderr + missing.stdout).lower()
    assert (repo_dir / "agy/settings.json").read_text() == '{"theme":"repo"}\n'

    cli_root = home_dir / ".gemini/antigravity-cli"
    write(cli_root / "mcp_config.json", '{"live":true}\n')
    write(cli_root / "skills/live/SKILL.md", "live skill\n")
    no_settings = run_script(repo_dir, home_dir, "init", "antigravity-cli")
    assert no_settings.returncode == 0, no_settings.stderr + no_settings.stdout
    assert (repo_dir / "agy/settings.json").read_text() == '{"theme":"repo"}\n'
    assert (repo_dir / "agy/mcp_config.json").read_text() == '{"repo":true}\n'
    assert (repo_dir / "agy/skills/repo/SKILL.md").read_text() == "repo skill\n"
    assert not (repo_dir / "agy/skills/live").exists()

    write(cli_root / "settings.json", '{"theme":"live"}\n')
    present = run_script(repo_dir, home_dir, "init", "antigravity-cli")
    assert present.returncode == 0, present.stderr + present.stdout
    assert (repo_dir / "agy/settings.json").read_text() == '{"theme":"live"}\n'
    assert not (home_dir / ".ai-config-backup").exists()


def test_status_codex_ignores_project_tables_and_reports_no_differences(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(
        repo_dir / "codex/config.toml",
        'model = "same"\n\n[features]\nsearch = true\n',
    )
    write(
        home_dir / ".codex/config.toml",
        'model = "same"\n\n'
        '[projects."C:/local"]\ntrust_level = "trusted"\n\n'
        '[features]\nsearch = true\n\n'
        '[projects."C:/other"]\ntrust_level = "untrusted"\n',
    )
    run_script(repo_dir, home_dir, "help")
    before_repo = snapshot_tree(repo_dir)
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Status: codex" in result.stdout
    assert "No differences found" in result.stdout
    assert "~ config.toml" not in result.stdout
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(home_dir) == before_home


def test_apply_all_projects_repo_configuration_to_tool_homes(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo with spaces 中文"
    home_dir = tmp_path / "home with spaces 中文"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "claude instructions\n")
    write(repo_dir / "claude/settings.json", '{"theme":"dark"}\n')
    write(repo_dir / "claude/mcp.json", '{"mcpServers":{"demo":{}}}\n')
    write(repo_dir / "claude/rules/common.md", "claude rule\n")
    write(
        repo_dir / "claude/agents/reviewer.md",
        "---\nname: reviewer\ndescription: Reviews code\n---\nReview carefully.\n",
    )
    write(repo_dir / "claude/commands/check.md", "Run checks.\n")
    write(repo_dir / "codex/config.toml", 'personality = "gpt-5"\n')
    write(repo_dir / "codex/rules/codex.md", "codex rule\n")
    write(
        repo_dir / "codex/skills/codex-only/SKILL.md",
        "---\nname: codex-only\ndescription: Codex only\n---\nCodex body.\n",
    )
    write(repo_dir / "agy/settings.json", '{"theme":"neon"}\n')
    write(
        repo_dir / "claude/shared/both/shared-skill/SKILL.md",
        "---\nname: shared-skill\ndescription: Shared skill\n---\nShared body.\n",
    )
    write(
        home_dir / ".codex/config.toml",
        'personality = "old-model"\n\n'
        '[projects."C:/workspace/專案 one"]\n'
        'trust_level = "trusted"\n',
    )

    result = run_script(repo_dir, home_dir, "apply", "all")

    assert result.returncode == 0, result.stderr + result.stdout
    expected_files = {
        ".claude/CLAUDE.md": "claude instructions\n",
        ".claude/settings.json": '{"theme":"dark"}\n',
        ".claude/mcp.json": '{"mcpServers":{"demo":{}}}\n',
        ".claude/rules/common.md": "claude rule\n",
        ".claude/agents/reviewer.md": (
            "---\nname: reviewer\ndescription: Reviews code\n---\nReview carefully.\n"
        ),
        ".claude/commands/check.md": "Run checks.\n",
        ".codex/AGENTS.md": "claude instructions\n",
        ".codex/rules/common.md": "claude rule\n",
        ".codex/rules/codex.md": "codex rule\n",
        ".gemini/antigravity-cli/settings.json": '{"theme":"neon"}\n',
        ".gemini/antigravity-cli/mcp_config.json": '{"mcpServers":{"demo":{}}}\n',
    }
    for relative_path, expected in expected_files.items():
        assert (home_dir / relative_path).read_text(encoding="utf-8") == expected

    codex_config = (home_dir / ".codex/config.toml").read_text(encoding="utf-8")
    assert 'personality = "gpt-5"' in codex_config
    assert 'personality = "old-model"' not in codex_config
    assert '[projects."C:/workspace/專案 one"]' in codex_config
    assert 'trust_level = "trusted"' in codex_config

    projected_skills = (
        ".agents/skills/codex-only/SKILL.md",
        ".agents/skills/reviewer/SKILL.md",
        ".agents/skills/shared-skill/SKILL.md",
        ".gemini/antigravity-cli/skills/reviewer/SKILL.md",
        ".gemini/antigravity-cli/skills/shared-skill/SKILL.md",
    )
    for relative_path in projected_skills:
        skill = (home_dir / relative_path).read_text(encoding="utf-8")
        assert skill.startswith("---\n")
        assert "\nname:" in skill
        assert "\ndescription:" in skill
        assert "\nmetadata:\n" in skill
        assert "\n  short-description:" in skill


def test_apply_sanitizes_frontmatter_as_valid_yaml(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(
        repo_dir / "claude/shared/both/yaml-edge/SKILL.md",
        "---\n"
        "name: yaml-edge\n"
        'description: Handles colon: "quotes" and # hashes\n'
        "metadata:\n"
        "  owner: platform\n"
        "license: MIT\n"
        "---\n"
        "Edge body.\n",
    )
    write(
        repo_dir / "claude/shared/both/generated-edge/SKILL.md",
        '# Generated: "quoted" # heading\n\nGenerated body.\n',
    )

    result = run_script(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    edge_content = (
        home_dir / ".agents/skills/yaml-edge/SKILL.md"
    ).read_text(encoding="utf-8")
    edge = load_frontmatter(edge_content)
    assert edge["name"] == "yaml-edge"
    assert edge["description"] == 'Handles colon: "quotes" and # hashes'
    assert edge["metadata"] == {
        "owner": "platform",
        "short-description": 'Handles colon: "quotes" and # hashes',
    }
    assert edge["license"] == "MIT"

    generated_content = (
        home_dir / ".agents/skills/generated-edge/SKILL.md"
    ).read_text(encoding="utf-8")
    generated = load_frontmatter(generated_content)
    assert generated["name"] == 'Generated: "quoted" # heading'
    assert generated["description"] == 'Generated: "quoted" # heading'
    assert generated["metadata"]["short-description"] == (
        'Generated: "quoted" # heading'
    )


def test_quoted_description_produces_complete_short_description(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(
        repo_dir / "claude/shared/both/quoted/SKILL.md",
        "---\n"
        "name: quoted\n"
        'description: "First. Second"\n'
        "---\n"
        "Quoted body.\n",
    )

    result = run_script(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    content = (
        home_dir / ".agents/skills/quoted/SKILL.md"
    ).read_text(encoding="utf-8")
    frontmatter = load_frontmatter(content)
    assert frontmatter["description"] == "First. Second"
    assert frontmatter["metadata"]["short-description"] == "First"


def test_later_skill_source_fully_replaces_earlier_source(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(
        repo_dir / "codex/skills/collision/SKILL.md",
        "---\nname: collision\ndescription: Earlier\n---\nEarlier body.\n",
    )
    write(
        repo_dir / "codex/skills/collision/examples/obsolete.md",
        "obsolete\n",
    )
    write(
        repo_dir / "claude/shared/both/collision/SKILL.md",
        "---\nname: collision\ndescription: Later\n---\nLater body.\n",
    )

    result = run_script(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    projected = home_dir / ".agents/skills/collision"
    skill = (projected / "SKILL.md").read_text(encoding="utf-8")
    assert "Later body." in skill
    assert "Earlier body." not in skill
    assert not (projected / "examples/obsolete.md").exists()


def test_apply_claude_mirrors_managed_dirs_without_touching_credentials(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "claude/rules/current.md", "current rule\n")
    write(repo_dir / "claude/agents/current.md", "current agent\n")
    write(repo_dir / "claude/commands/current.md", "current command\n")
    write(repo_dir / "claude/rules/auth.json", "source credential\n")
    write(repo_dir / "claude/agents/oauth_creds.json", "source credential\n")
    write(repo_dir / "claude/commands/google_accounts.json", "source credential\n")
    write(repo_dir / "claude/commands/trustedFolders.json", "source credential\n")

    write(home_dir / ".claude/rules/stale.md", "stale\n")
    write(home_dir / ".claude/agents/stale.md", "stale\n")
    write(home_dir / ".claude/commands/stale.md", "stale\n")
    preserved = {
        ".claude/rules/auth.json": "live auth\n",
        ".claude/rules/nested/.credentials.json": "live credentials\n",
        ".claude/agents/oauth_creds.json": "live oauth\n",
        ".claude/commands/trustedFolders.json": "live trusted folders\n",
    }
    for relative_path, content in preserved.items():
        write(home_dir / relative_path, content)

    result = run_script(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    for managed_dir in ("rules", "agents", "commands"):
        assert not (home_dir / f".claude/{managed_dir}/stale.md").exists()
    assert (home_dir / ".claude/rules/current.md").read_text() == "current rule\n"
    assert (home_dir / ".claude/agents/current.md").read_text() == "current agent\n"
    assert (home_dir / ".claude/commands/current.md").read_text() == "current command\n"
    for relative_path, content in preserved.items():
        assert (home_dir / relative_path).read_text() == content
    assert not (home_dir / ".claude/commands/google_accounts.json").exists()


def test_apply_backs_up_only_managed_paths_and_keeps_latest_five(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "new claude\n")
    write(repo_dir / "codex/config.toml", 'model = "new"\n')
    write(repo_dir / "agy/settings.json", '{"theme":"new"}\n')

    write(home_dir / ".claude/CLAUDE.md", "old claude\n")
    write(home_dir / ".claude/rules/old.md", "old rule\n")
    write(home_dir / ".claude/runtime-cache/cache.bin", "runtime\n")
    credential_paths = (
        ".claude/rules/.credentials.json",
        ".claude/rules/auth.json",
        ".claude/agents/oauth_creds.json",
        ".claude/commands/google_accounts.json",
        ".claude/commands/trustedFolders.json",
    )
    for relative_path in credential_paths:
        write(home_dir / relative_path, f"live {Path(relative_path).name}\n")
    write(home_dir / ".codex/AGENTS.md", "old agents\n")
    write(home_dir / ".codex/config.toml", 'model = "old"\n')
    write(home_dir / ".codex/sessions/session.json", "runtime\n")
    write(home_dir / ".agents/skills/hand-installed/SKILL.md", "hand installed\n")
    write(
        home_dir / ".gemini/antigravity-cli/settings.json",
        '{"theme":"old"}\n',
    )
    write(
        home_dir / ".gemini/antigravity-cli/plugins/installed.json",
        "old plugin\n",
    )
    write(
        home_dir / ".gemini/antigravity-cli/browser/cache.bin",
        "runtime\n",
    )

    first = run_script(repo_dir, home_dir, "apply", "all")

    assert first.returncode == 0, first.stderr + first.stdout
    backup_root = home_dir / ".ai-config-backup"
    snapshots = sorted(path for path in backup_root.iterdir() if path.is_dir())
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert (snapshot / "claude/CLAUDE.md").read_text() == "old claude\n"
    assert not (snapshot / "claude/rules").exists()
    assert (home_dir / ".claude/rules/old.md").read_text() == "old rule\n"
    assert (snapshot / "codex/AGENTS.md").read_text() == "old agents\n"
    assert (snapshot / "codex/config.toml").read_text() == 'model = "old"\n'
    assert (
        snapshot / "codex/skills/hand-installed/SKILL.md"
    ).read_text() == "hand installed\n"
    assert (
        snapshot / "agy/settings.json"
    ).read_text() == '{"theme":"old"}\n'
    assert not (snapshot / "agy/plugins").exists()
    assert not (snapshot / "claude/runtime-cache").exists()
    assert not (snapshot / "codex/sessions").exists()
    assert not (snapshot / "agy/browser").exists()
    for relative_path in credential_paths:
        managed_relative = Path(relative_path).relative_to(".claude")
        assert not (snapshot / "claude" / managed_relative).exists()
        assert (home_dir / relative_path).read_text() == (
            f"live {Path(relative_path).name}\n"
        )

    for _ in range(5):
        result = run_script(repo_dir, home_dir, "apply", "all")
        assert result.returncode == 0, result.stderr + result.stdout

    snapshots = sorted(path for path in backup_root.iterdir() if path.is_dir())
    assert len(snapshots) == 5


def test_managed_skills_use_allowlist_and_prune_only_manifest_orphans(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "codex/config.toml", 'model = "test"\n')
    for skill_name in ("current", "removed-later"):
        write(
            repo_dir / f"codex/skills/{skill_name}/SKILL.md",
            f"---\nname: {skill_name}\ndescription: Test\n---\nBody.\n",
        )
    write(repo_dir / "codex/skills/current/examples/example.md", "example\n")
    write(repo_dir / "codex/skills/current/references/reference.md", "reference\n")
    write(repo_dir / "codex/skills/current/scripts/run.ps1", "Write-Output ok\n")
    write(repo_dir / "codex/skills/current/agents/helper.md", "helper\n")
    write(repo_dir / "codex/skills/current/notes.txt", "notes\n")
    write(repo_dir / "codex/skills/current/assets/logo.md", "logo\n")
    write(home_dir / ".agents/skills/hand-installed/SKILL.md", "hand installed\n")

    first = run_script(repo_dir, home_dir, "apply", "codex")

    assert first.returncode == 0, first.stderr + first.stdout
    skills = home_dir / ".agents/skills"
    assert (skills / ".ai-config-managed").read_text().splitlines() == [
        "current",
        "removed-later",
    ]
    for relative_path in (
        "SKILL.md",
        "examples/example.md",
        "references/reference.md",
        "scripts/run.ps1",
        "agents/helper.md",
        # 整個技能都帶過去,不只上面四個資料夾
        "notes.txt",
        "assets/logo.md",
    ):
        assert (skills / "current" / relative_path).is_file()
    write(skills / "current/stale-managed.txt", "stale\n")
    write(skills / "current/.credentials.json", "live secret\n")

    shutil.rmtree(repo_dir / "codex/skills/removed-later")
    second = run_script(repo_dir, home_dir, "apply", "codex")

    assert second.returncode == 0, second.stderr + second.stdout
    assert (skills / ".ai-config-managed").read_text().splitlines() == ["current"]
    assert not (skills / "removed-later").exists()
    assert not (skills / "current/stale-managed.txt").exists()
    assert (skills / "current/.credentials.json").read_text() == "live secret\n"
    assert (skills / "hand-installed/SKILL.md").read_text() == "hand installed\n"


def test_codex_status_reads_legacy_skills_before_migration(tmp_path: Path) -> None:
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
    first = run_script(repo_dir, home_dir, "apply", "codex")
    assert first.returncode == 0, first.stderr + first.stdout
    canonical = home_dir / ".agents/skills"
    legacy = home_dir / ".codex/skills"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    canonical.rename(legacy)

    status = run_script(repo_dir, home_dir, "status", "codex")

    assert status.returncode == 0, status.stderr + status.stdout
    assert "No differences found" in status.stdout


def test_agy_skills_use_canonical_store_and_update_safe_fallback(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "agy/settings.json", '{"theme":"test"}\n')
    skill_source = repo_dir / "claude/shared/both/demo/SKILL.md"
    write(
        skill_source,
        "---\nname: demo\ndescription: Demo\n---\nVersion one.\n",
    )
    write(
        home_dir / ".gemini/config/skills/hand-installed/SKILL.md",
        "hand installed\n",
    )

    first = run_script(repo_dir, home_dir, "apply", "agy")

    assert first.returncode == 0, first.stderr + first.stdout
    canonical = home_dir / ".gemini/config/skills"
    cli = home_dir / ".gemini/antigravity-cli"
    assert "Version one." in (canonical / "demo/SKILL.md").read_text()
    assert (canonical / "hand-installed/SKILL.md").read_text() == "hand installed\n"
    assert "Version one." in (cli / "skills/demo/SKILL.md").read_text()
    assert (cli / ".ai-config-skills-mirror").read_text() == "skills\n"
    snapshots = [
        path
        for path in (home_dir / ".ai-config-backup").iterdir()
        if path.is_dir()
    ]
    assert len(snapshots) == 1
    assert (
        snapshots[0] / "agy/skills/hand-installed/SKILL.md"
    ).read_text() == "hand installed\n"

    write(
        skill_source,
        "---\nname: demo\ndescription: Demo\n---\nVersion two.\n",
    )
    second = run_script(repo_dir, home_dir, "apply", "agy")

    assert second.returncode == 0, second.stderr + second.stdout
    assert "Version two." in (canonical / "demo/SKILL.md").read_text()
    assert "Version two." in (cli / "skills/demo/SKILL.md").read_text()


def test_agy_migrates_owned_legacy_copy_to_current_canonical(tmp_path: Path) -> None:
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
    legacy = home_dir / ".gemini/antigravity/skills"
    write(legacy / "hand-installed/SKILL.md", "hand installed\n")
    cli = home_dir / ".gemini/antigravity-cli"
    shutil.copytree(legacy, cli / "skills")
    write_skills_ownership(cli, legacy, "directory")

    result = run_script(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    canonical = home_dir / ".gemini/config/skills"
    assert (canonical / "hand-installed/SKILL.md").is_file()
    assert (canonical / "demo/SKILL.md").is_file()
    assert (cli / "skills/hand-installed/SKILL.md").is_file()
    assert (cli / "skills/demo/SKILL.md").is_file()
    current_state = json.loads(
        (cli / ".ai-config-skills-state.json").read_text(encoding="utf-8")
    )
    assert current_state["entries"][0]["source"] == str(canonical.absolute())


def test_agy_fallback_preserves_tampered_managed_skills(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "agy/settings.json", '{"theme":"test"}\n')
    skill_source = repo_dir / "claude/shared/both/demo/SKILL.md"
    write(
        skill_source,
        "---\nname: demo\ndescription: Demo\n---\nVersion one.\n",
    )

    first = run_script(repo_dir, home_dir, "apply", "agy")

    assert first.returncode == 0, first.stderr + first.stdout
    cli = home_dir / ".gemini/antigravity-cli"
    assert (cli / ".ai-config-skills-state.json").is_file()
    (cli / ".ai-config-skills-mirror").unlink()
    write(cli / "skills/demo/SKILL.md", "manual skill\n")
    write(
        skill_source,
        "---\nname: demo\ndescription: Demo\n---\nVersion two.\n",
    )

    second = run_script(repo_dir, home_dir, "apply", "agy")

    assert second.returncode == 0, second.stderr + second.stdout
    assert "ownership/content changed" in second.stderr + second.stdout
    assert (cli / "skills/demo/SKILL.md").read_text() == "manual skill\n"


def test_agy_skills_do_not_overwrite_unmarked_cli_conflict(tmp_path: Path) -> None:
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
    write(
        home_dir / ".gemini/antigravity-cli/skills/local/SKILL.md",
        "unmanaged local\n",
    )
    write(
        home_dir / ".gemini/antigravity-cli/.ai-config-skills-mirror",
        "skills\n",
    )

    result = run_script(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "unmanaged Antigravity skills path" in result.stderr + result.stdout
    cli_skills = home_dir / ".gemini/antigravity-cli/skills"
    assert (cli_skills / "local/SKILL.md").read_text() == "unmanaged local\n"
    assert not (cli_skills / "demo").exists()
    assert (
        home_dir / ".gemini/config/skills/demo/SKILL.md"
    ).is_file()


def test_claude_skills_and_plugins_project_to_codex_and_agy(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home with spaces 中文"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(
        repo_dir / "claude/skills/live-skill/SKILL.md",
        "---\nname: live-skill\ndescription: Live\n---\nLive skill.\n",
    )
    source_plugins = home_dir / ".claude/plugins"
    target_plugins = home_dir / ".gemini/antigravity-cli/plugins"
    write(
        repo_dir / "claude/plugins/installed_plugins.json",
        json.dumps({"installPath": str(source_plugins)}, ensure_ascii=False) + "\n",
    )
    write(repo_dir / "claude/plugins/demo/plugin.txt", "plugin data\n")
    write(repo_dir / "claude/plugins/demo/.credentials.json", "source secret\n")
    write(target_plugins / "demo/.credentials.json", "live secret\n")

    result = run_script(repo_dir, home_dir, "apply", "all")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (home_dir / ".agents/skills/live-skill/SKILL.md").is_file()
    assert (
        home_dir / ".gemini/config/skills/live-skill/SKILL.md"
    ).is_file()
    assert (
        home_dir / ".gemini/antigravity-cli/skills/live-skill/SKILL.md"
    ).is_file()
    assert (target_plugins / "demo/plugin.txt").read_text() == "plugin data\n"
    assert (
        target_plugins / "demo/.credentials.json"
    ).read_text() == "live secret\n"
    installed = json.loads(
        (target_plugins / "installed_plugins.json").read_text(encoding="utf-8")
    )
    assert installed["installPath"] == str(target_plugins)


def test_skill_manifest_rejects_traversal_and_only_prunes_safe_orphan(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)

    write(repo_dir / "codex/config.toml", 'model = "safe"\n')
    outside_owned = home_dir / "outside-owned"
    outside_win = home_dir / "outside-win"
    absolute_owned = tmp_path / "absolute-owned"
    for path, content in (
        (outside_owned / "keep.txt", "outside relative\n"),
        (outside_win / "keep.txt", "outside windows\n"),
        (absolute_owned / "keep.txt", "outside absolute\n"),
    ):
        write(path, content)
    skills = home_dir / ".agents/skills"
    write(skills / "legal-orphan/SKILL.md", "legal orphan\n")
    write(
        skills / ".ai-config-managed",
        "../../outside-owned\n"
        "..\\..\\outside-win\n"
        f"{absolute_owned}\n"
        "legal-orphan\n",
    )

    result = run_script(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Ignoring unsafe managed skill name" in result.stderr + result.stdout
    assert (outside_owned / "keep.txt").read_text() == "outside relative\n"
    assert (outside_win / "keep.txt").read_text() == "outside windows\n"
    assert (absolute_owned / "keep.txt").read_text() == "outside absolute\n"
    assert not (skills / "legal-orphan").exists()


def test_parallel_apply_uses_distinct_completed_snapshots(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "managed instructions\n")
    for index in range(50):
        write(repo_dir / f"claude/rules/rule-{index}.md", f"rule {index}\n")
        write(home_dir / f".claude/rules/old-{index}.md", f"old {index}\n")

    command = [sys.executable, "-m", "ai_config", "apply", "claude"]
    env = make_env(home_dir)
    env["AI_CONFIG_PLATFORM"] = "windows"
    processes = [
        subprocess.Popen(
            command,
            cwd=repo_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]

    for process, (stdout, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stderr + stdout
    backup_root = home_dir / ".ai-config-backup"
    completed = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and (path / ".ai-config-backup-owned").is_file()
    ]
    assert len(completed) == 2
    assert len({path.name for path in completed}) == 2
    assert not list(backup_root.glob(".tmp-*"))
    assert (home_dir / ".claude/CLAUDE.md").read_text() == "managed instructions\n"
    for index in range(50):
        assert (home_dir / f".claude/rules/rule-{index}.md").read_text() == (
            f"rule {index}\n"
        )
