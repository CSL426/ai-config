"""Windows CLI contract: usage, init, status, list, project and reset."""

import hashlib
import re
import tempfile
from pathlib import Path

from windows_test_helpers import (
    REPO_ROOT,
    copy_runtime_files,
    run_script,
    snapshot_tree,
    write,
)


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
    write(repo_dir / "codex/config.toml", 'personality = "repo-codex"\n')
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
    write(home_dir / ".codex/config.toml", 'personality = "before-project"\n')
    write(
        home_dir / ".gemini/antigravity-cli/settings.json",
        '{"theme":"before-project"}\n',
    )
    before_repo = snapshot_tree(repo_dir)
    before_claude = snapshot_tree(live_claude)

    result = run_script(repo_dir, home_dir, "project", "all")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (home_dir / ".codex/AGENTS.md").read_text() == "live instructions\n"
    assert 'personality = "repo-codex"' in (home_dir / ".codex/config.toml").read_text()
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

    # 沒拿到確認就沒重設:離開碼要讓腳本分得出來
    assert default.returncode == 1, default.stderr + default.stdout
    assert no.returncode == 1, no.stderr + no.stdout
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
    expected_hash = hashlib.sha256(
        source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    ).hexdigest()
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
    source_hash = hashlib.sha256(
        source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    ).hexdigest()
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
