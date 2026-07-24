import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_frontmatter(content: str) -> dict[str, object]:
    assert content.startswith("---\n")
    frontmatter, separator, _ = content[4:].partition("\n---\n")
    assert separator
    parsed = yaml.safe_load(frontmatter)
    assert isinstance(parsed, dict)
    return parsed


def make_env(
    home_dir: Path, *, force_copy_fallback: bool = True
) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["XDG_CONFIG_HOME"] = str(home_dir / ".config")
    env["XDG_DATA_HOME"] = str(home_dir / ".local/share")
    env["XDG_CACHE_HOME"] = str(home_dir.parent / ".runtime-cache")
    if force_copy_fallback:
        env["AI_CONFIG_FORCE_COPY_FALLBACK"] = "1"
    return env


def run_script(
    repo_dir: Path,
    home_dir: Path,
    *args: str,
    input_text: str | None = None,
    force_copy_fallback: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "ai_config", *args]
    env = make_env(home_dir, force_copy_fallback=force_copy_fallback)
    env["AI_CONFIG_PLATFORM"] = "windows"
    return subprocess.run(
        command,
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        input=input_text,
        check=False,
    )


def copy_runtime_files(repo_dir: Path) -> None:
    shutil.copytree(REPO_ROOT / "ai_config", repo_dir / "ai_config")


def snapshot_tree(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for path in sorted((root, *root.rglob("*"))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def directory_fingerprint(path: Path) -> str:
    records = []
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(path).as_posix()
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        records.append(f"{relative}\0{digest}")
    records.sort(key=str.casefold)
    return hashlib.sha256("\n".join(records).encode()).hexdigest()


def write_skills_ownership(cli: Path, source: Path, kind: str) -> None:
    entry = {
        "version": 1,
        "path": "skills",
        "source": str(source.absolute()),
        "kind": kind,
    }
    if kind == "directory":
        entry["fingerprint"] = directory_fingerprint(cli / "skills")
    elif kind == "junction":
        entry["target"] = str(source.absolute())
    else:
        raise ValueError(f"Unsupported ownership kind: {kind}")
    state = {"version": 1, "entries": [entry]}
    write(cli / ".ai-config-skills-state.json", json.dumps(state))
    write(cli / ".ai-config-skills-mirror", "skills\n")


def test_no_arguments_and_help_show_windows_usage_commands_and_tools(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    for args in ((), ("help",), ("--help",), ("-h",)):
        result = run_script(REPO_ROOT, home_dir, *args)

        assert result.returncode == 0, result.stderr + result.stdout
        assert ".\\ai-config.ps1 <command> [tool]" in result.stdout
        for command in ("init", "apply", "project", "status", "list", "reset"):
            assert command in result.stdout
        for tool in ("claude", "codex", "agy", "all"):
            assert tool in result.stdout


def test_unknown_command_and_tool_fail(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    unknown_command = run_script(REPO_ROOT, home_dir, "explode")
    unknown_tool = run_script(REPO_ROOT, home_dir, "apply", "mystery")

    assert unknown_command.returncode != 0
    assert "Unknown command" in unknown_command.stderr + unknown_command.stdout
    assert unknown_tool.returncode != 0
    assert "Unknown tool" in unknown_tool.stderr + unknown_tool.stdout


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


def test_init_claude_mirrors_only_managed_paths_and_preserves_credentials(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    root_files = {
        "CLAUDE.md": b"root claude\n",
        "AGENTS.md": b"root agents\n",
        "GEMINI.md": b"root gemini\n",
    }
    for name, content in root_files.items():
        (repo_dir / name).write_bytes(content)
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")
    write(home_dir / ".claude/settings.json", '{"theme":"live"}\n')
    write(home_dir / ".claude/rules/current.md", "current rule\n")
    write(home_dir / ".claude/commands/current.md", "current command\n")
    write(repo_dir / "claude/mcp.json", '{"stale":true}\n')
    write(repo_dir / "claude/rules/stale.md", "stale rule\n")
    write(repo_dir / "claude/agents/stale.md", "stale agent\n")
    write(repo_dir / "claude/commands/stale.md", "stale command\n")
    credential_names = (
        ".credentials.json",
        "auth.json",
        "oauth_creds.json",
        "google_accounts.json",
        "trustedFolders.json",
    )
    for name in credential_names:
        write(home_dir / ".claude/rules" / name, "live credential\n")
        write(repo_dir / "claude/rules" / name, "repo credential\n")

    result = run_script(repo_dir, home_dir, "init", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (repo_dir / "claude/CLAUDE.md").read_text() == "live instructions\n"
    assert (repo_dir / "claude/settings.json").read_text() == '{"theme":"live"}\n'
    assert not (repo_dir / "claude/mcp.json").exists()
    assert (repo_dir / "claude/rules/current.md").read_text() == "current rule\n"
    assert not (repo_dir / "claude/rules/stale.md").exists()
    assert not (repo_dir / "claude/agents/stale.md").exists()
    assert (repo_dir / "claude/commands/current.md").read_text() == "current command\n"
    assert not (repo_dir / "claude/commands/stale.md").exists()
    for name in credential_names:
        assert (repo_dir / "claude/rules" / name).read_text() == "repo credential\n"
    for name, content in root_files.items():
        assert (repo_dir / name).read_bytes() == content
    assert not (home_dir / ".ai-config-backup").exists()


def test_init_claude_requires_live_directory_without_mutating_repo(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    before = snapshot_tree(repo_dir)

    result = run_script(repo_dir, home_dir, "init", "claude")

    assert result.returncode != 0
    assert "not found" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before


def test_init_claude_preflights_all_top_files_before_repo_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-settings.json"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    write(repo_dir / "claude/mcp.json", '{"stale":true}\n')
    write(repo_dir / "claude/settings.json", '{"theme":"repo"}\n')
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")
    write(external, '{"sensitive":"external"}\n')
    settings = home_dir / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.symlink_to(external)
    before_repo = snapshot_tree(repo_dir)
    before_external = external.read_bytes()

    result = run_script(repo_dir, home_dir, "init", "claude")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert external.read_bytes() == before_external
    assert b"sensitive" not in (repo_dir / "claude/settings.json").read_bytes()


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
        b'model = "live"\n\n'
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
        b'model = "live"\n\n'
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


def test_status_reports_missing_and_different_files_without_mutation(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "same instructions\n")
    write(repo_dir / "claude/settings.json", '{"theme":"repo"}\n')
    write(repo_dir / "claude/rules/missing.md", "missing rule\n")
    write(home_dir / ".claude/CLAUDE.md", "same instructions\n")
    write(home_dir / ".claude/settings.json", '{"theme":"live"}\n')
    run_script(repo_dir, home_dir, "help")
    before_repo = snapshot_tree(repo_dir)
    before_home = snapshot_tree(home_dir)
    before_stages = set(Path(tempfile.gettempdir()).glob("ai-config-status-*"))

    result = run_script(repo_dir, home_dir, "status", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Status: claude" in result.stdout
    assert "~ settings.json" in result.stdout
    assert "+ rules/missing.md" in result.stdout
    assert "Status: codex" not in result.stdout
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(home_dir) == before_home
    assert set(Path(tempfile.gettempdir()).glob("ai-config-status-*")) == before_stages


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


def test_status_all_uses_each_projection_and_remains_read_only(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    write(repo_dir / "agy/settings.json", '{"theme":"repo"}\n')
    (home_dir / ".claude").mkdir()
    (home_dir / ".codex").mkdir()
    (home_dir / ".gemini/antigravity-cli").mkdir(parents=True)
    run_script(repo_dir, home_dir, "help")
    before_repo = snapshot_tree(repo_dir)
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "status", "all")

    assert result.returncode == 0, result.stderr + result.stdout
    for tool in ("claude", "codex", "agy"):
        assert f"Status: {tool}" in result.stdout
    assert "+ CLAUDE.md" in result.stdout
    assert "+ config.toml" in result.stdout
    assert "+ settings.json" in result.stdout
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(home_dir) == before_home
    assert not (home_dir / ".ai-config-backup").exists()


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


def test_project_all_uses_live_claude_and_repo_tool_specific_and_shared_sources(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions must not project\n")
    write(repo_dir / "claude/rules/repo-only.md", "repo claude rule\n")
    write(repo_dir / "codex/config.toml", 'model = "repo-codex"\n')
    write(repo_dir / "codex/rules/codex.md", "codex rule\n")
    write(repo_dir / "codex/skills/codex-only/SKILL.md", "# Codex only\n")
    write(repo_dir / "agy/settings.json", '{"theme":"repo-agy"}\n')
    write(repo_dir / "agy/skills/agy-only/SKILL.md", "# Agy only\n")
    for scope in ("both", "codex", "agy"):
        write(
            repo_dir / f"claude/shared/{scope}/shared-{scope}/SKILL.md",
            f"# Shared {scope}\n",
        )
    live_claude = home_dir / ".claude"
    write(live_claude / "CLAUDE.md", "live instructions\n")
    write(live_claude / "mcp.json", '{"mcpServers":{"live":{}}}\n')
    write(live_claude / "rules/live.md", "live rule\n")
    write(live_claude / "agents/reviewer.md", "# Reviewer\nReview live.\n")
    write(live_claude / "skills/live-skill/SKILL.md", "# Live skill\n")
    write(live_claude / "plugins/live/plugin.txt", "live plugin\n")
    write(
        live_claude / "shared/both/live-shared/SKILL.md",
        "# Must not be shared source\n",
    )
    write(home_dir / ".codex/config.toml", 'model = "before-project"\n')
    write(
        home_dir / ".gemini/antigravity-cli/settings.json",
        '{"theme":"before-project"}\n',
    )
    before_repo = snapshot_tree(repo_dir)
    before_claude = snapshot_tree(live_claude)

    result = run_script(repo_dir, home_dir, "project", "all")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (home_dir / ".codex/AGENTS.md").read_text() == "live instructions\n"
    assert 'model = "repo-codex"' in (home_dir / ".codex/config.toml").read_text()
    assert (home_dir / ".codex/rules/live.md").read_text() == "live rule\n"
    assert (home_dir / ".codex/rules/codex.md").read_text() == "codex rule\n"
    codex_skills = home_dir / ".agents/skills"
    for name in ("reviewer", "live-skill", "codex-only", "shared-both", "shared-codex"):
        assert (codex_skills / name / "SKILL.md").is_file()
    assert not (codex_skills / "shared-agy").exists()
    assert not (codex_skills / "live-shared").exists()
    agy_root = home_dir / ".gemini/antigravity-cli"
    assert (agy_root / "settings.json").read_text() == '{"theme":"repo-agy"}\n'
    assert (agy_root / "mcp_config.json").read_text() == '{"mcpServers":{"live":{}}}\n'
    assert (agy_root / "plugins/live/plugin.txt").read_text() == "live plugin\n"
    for name in ("reviewer", "live-skill", "agy-only", "shared-both", "shared-agy"):
        assert (agy_root / "skills" / name / "SKILL.md").is_file()
    assert not (agy_root / "skills/shared-codex").exists()
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(live_claude) == before_claude
    snapshots = [
        path
        for path in (home_dir / ".ai-config-backup").iterdir()
        if path.is_dir() and not path.name.startswith(".tmp-")
    ]
    assert len(snapshots) == 1


def test_project_supports_single_targets_and_antigravity_alias(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    write(repo_dir / "agy/settings.json", '{"theme":"repo"}\n')

    codex = run_script(repo_dir, home_dir, "project", "codex")
    assert codex.returncode == 0, codex.stderr + codex.stdout
    assert (home_dir / ".codex/AGENTS.md").is_file()
    assert not (home_dir / ".gemini/antigravity-cli/settings.json").exists()

    agy = run_script(repo_dir, home_dir, "project", "antigravity-cli")
    assert agy.returncode == 0, agy.stderr + agy.stdout
    assert (home_dir / ".gemini/antigravity-cli/settings.json").is_file()


def test_project_claude_warns_without_mutation(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")
    before_repo = snapshot_tree(repo_dir)
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "project", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "no tools" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(home_dir) == before_home


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


def test_reset_default_and_no_cancel_without_mutation(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "codex/config.toml", 'model = "repo"\n')
    write(repo_dir / "agy/settings.json", '{"theme":"repo"}\n')
    before = snapshot_tree(repo_dir)

    default = run_script(repo_dir, home_dir, "reset", input_text="\n")
    no = run_script(repo_dir, home_dir, "reset", input_text="n\n")

    assert default.returncode == 0, default.stderr + default.stdout
    assert no.returncode == 0, no.stderr + no.stdout
    assert "cancelled" in (default.stderr + default.stdout).lower()
    assert "cancelled" in (no.stderr + no.stdout).lower()
    assert snapshot_tree(repo_dir) == before


def test_reset_yes_clears_files_and_links_but_preserves_directory_skeleton(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external_dir = tmp_path / "external-dir"
    external_file = tmp_path / "external-file.txt"
    repo_dir.mkdir()
    home_dir.mkdir()
    external_dir.mkdir()
    copy_runtime_files(repo_dir)
    root_files = {
        "CLAUDE.md": b"root claude\n",
        "AGENTS.md": b"root agents\n",
        "GEMINI.md": b"root gemini\n",
        "README.md": b"readme\n",
    }
    for name, content in root_files.items():
        (repo_dir / name).write_bytes(content)
    write(repo_dir / "claude/rules/nested/rule.md", "rule\n")
    write(repo_dir / "claude/.hidden", "hidden\n")
    write(repo_dir / "codex/skills/demo/SKILL.md", "skill\n")
    write(repo_dir / "agy/settings.json", '{"theme":"repo"}\n')
    write(external_dir / "keep.md", "external directory\n")
    write(external_file, "external file\n")
    (repo_dir / "claude/rules/external-link").symlink_to(
        external_dir,
        target_is_directory=True,
    )
    (repo_dir / "agy/external-file-link").symlink_to(external_file)
    before_external_dir = snapshot_tree(external_dir)
    before_external_file = external_file.read_bytes()

    result = run_script(repo_dir, home_dir, "reset", input_text="Y\n")

    assert result.returncode == 0, result.stderr + result.stdout
    for relative in (
        "claude",
        "claude/rules",
        "claude/rules/nested",
        "codex",
        "codex/skills",
        "codex/skills/demo",
        "agy",
    ):
        assert (repo_dir / relative).is_dir()
        assert not (repo_dir / relative).is_symlink()
    for tool in ("claude", "codex", "agy"):
        entries = snapshot_tree(repo_dir / tool)
        assert all(kind == "directory" for kind, _ in entries.values())
    assert snapshot_tree(external_dir) == before_external_dir
    assert external_file.read_bytes() == before_external_file
    for name, content in root_files.items():
        assert (repo_dir / name).read_bytes() == content


def test_reset_preflights_all_tool_roots_before_any_deletion(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    external = tmp_path / "external-codex"
    repo_dir.mkdir()
    home_dir.mkdir()
    external.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "must remain\n")
    write(repo_dir / "agy/settings.json", '{"must":"remain"}\n')
    write(external / "config.toml", 'model = "external"\n')
    (repo_dir / "codex").symlink_to(external, target_is_directory=True)
    before_repo = snapshot_tree(repo_dir)
    before_external = snapshot_tree(external)

    result = run_script(repo_dir, home_dir, "reset", input_text="y\n")

    assert result.returncode != 0
    assert "reparse point" in (result.stderr + result.stdout).lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(external) == before_external


def test_status_reports_quoted_crlf_mirror_missing_and_mismatch_read_only(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(home_dir / ".claude/CLAUDE.md", "instructions\n")
    source = home_dir / "sources/source one.md"
    write(source, "current mirror source\n")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    stale_skill = repo_dir / "claude/shared/both/stale/SKILL.md"
    stale_skill.parent.mkdir(parents=True)
    stale_skill.write_bytes(
        b"---\r\n"
        b"name: stale\r\n"
        b"description: Stale mirror\r\n"
        b"metadata:\r\n"
        b'  mirror-of: \"~/sources/source one.md\"\r\n'
        b'  mirror-hash: \"0000000000000000000000000000000000000000000000000000000000000000\"\r\n'
        b"---\r\nBody.\r\n"
    )
    write(
        repo_dir / "claude/shared/codex/missing/SKILL.md",
        "---\n"
        "name: missing\n"
        "description: Missing mirror\n"
        "metadata:\n"
        "  mirror-of: '~/sources/missing.md'\n"
        f"  mirror-hash: '{expected_hash}'\n"
        "---\nBody.\n",
    )
    before_repo = snapshot_tree(repo_dir)
    before_home = snapshot_tree(home_dir)

    result = run_script(repo_dir, home_dir, "status", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    combined = result.stderr + result.stdout
    assert "mirror stale" in combined.lower()
    assert "mirror source missing" in combined.lower()
    assert expected_hash in combined
    assert "all 2 mirrored" not in combined.lower()
    assert snapshot_tree(repo_dir) == before_repo
    assert snapshot_tree(home_dir) == before_home


def test_status_reports_all_mirrors_consistent_summary(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "codex/config.toml", 'model = "same"\n')
    write(home_dir / ".codex/config.toml", 'model = "same"\n')
    source = home_dir / "source.md"
    write(source, "matching source\n")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    write(
        repo_dir / "claude/shared/agy/matching/SKILL.md",
        "---\n"
        "name: matching\n"
        "description: Matching mirror\n"
        "metadata:\n"
        '  mirror-of: "~/source.md"\n'
        f'  mirror-hash: "{source_hash.upper()}"\n'
        "---\nBody.\n",
    )

    result = run_script(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "All 1 mirrored shared skills up to date" in result.stdout


def test_status_without_mirrors_omits_mirror_summary(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(home_dir / ".claude/CLAUDE.md", "instructions\n")
    write(
        repo_dir / "claude/shared/both/ordinary/SKILL.md",
        "---\nname: ordinary\ndescription: No mirror\n---\nBody.\n",
    )

    result = run_script(repo_dir, home_dir, "status", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "mirrored shared skills" not in (result.stderr + result.stdout).lower()


def test_script_avoids_powershell_7_only_syntax() -> None:
    script = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    string_or_comment = re.compile(
        r"'(?:''|[^'])*'|\"(?:`.|[^\"`])*\"|#.*$",
        re.MULTILINE,
    )
    code = string_or_comment.sub("", script)
    forbidden = {
        "null-coalescing operator": re.compile(r"\?\?=?"),
        "and pipeline chain": re.compile(r"&&"),
        "or pipeline chain": re.compile(r"\|\|"),
        "parallel foreach": re.compile(
            r"\bForEach-Object\s+-Parallel\b",
            re.IGNORECASE,
        ),
    }

    for name, pattern in forbidden.items():
        assert pattern.search(code) is None, f"PowerShell 7-only {name} found"


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
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
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
        'model = "old-model"\n\n'
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
    assert 'model = "gpt-5"' in codex_config
    assert 'model = "old-model"' not in codex_config
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
    write(repo_dir / "codex/skills/current/ignored.txt", "ignored\n")
    write(repo_dir / "codex/skills/current/assets/ignored.md", "ignored\n")
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
    ):
        assert (skills / "current" / relative_path).is_file()
    assert not (skills / "current/ignored.txt").exists()
    assert not (skills / "current/assets").exists()
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
