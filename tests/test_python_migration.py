import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_apply_projection import IMPL, copy_runtime_files, run_ai_config, write


pytestmark = pytest.mark.skipif(IMPL != "py", reason="Python migration contract")


def make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    return repo_dir, home_dir


def test_incomplete_quote_uses_ps1_rewrite_and_name_fallback(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    write(
        repo_dir / "claude/agents/demo.md",
        "---\nname: demo\ndescription: \"unfinished\n---\nBody.\n",
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    normalized = (home_dir / ".agents/skills/demo/SKILL.md").read_text()
    assert "description: >-\n  \"unfinished\n" in normalized
    assert "  short-description: 'demo'\n" in normalized


def test_short_description_stays_inside_existing_metadata(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    write(
        repo_dir / "claude/agents/demo.md",
        "---\nname: demo\ndescription: Demo skill.\nmetadata:\n"
        "  owner: team\nlicense: MIT\n---\nBody.\n",
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    normalized = (home_dir / ".agents/skills/demo/SKILL.md").read_text()
    assert (
        "metadata:\n  owner: team\n  short-description: 'Demo skill'\nlicense: MIT\n"
        in normalized
    )


def test_frontmatter_uses_ps1_scalar_and_normalizes_newlines(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    agent = repo_dir / "claude/agents/bare-agent.md"
    agent.parent.mkdir(parents=True)
    agent.write_bytes(b"# Bare Agent\r\n\r\nDoes things.\r\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    skill = (home_dir / ".agents/skills/bare-agent/SKILL.md").read_bytes()
    assert b"name: 'Bare Agent'\n" in skill
    assert b"\r" not in skill


def test_invalid_frontmatter_fails_before_live_mutation(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    write(repo_dir / "claude/agents/broken.md", "---\nname: broken\n")
    write(home_dir / ".claude/CLAUDE.md", "old instructions\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "all")

    assert result.returncode == 1
    assert (home_dir / ".claude/CLAUDE.md").read_text() == "old instructions\n"
    assert not (home_dir / ".ai-config-backup").exists()


def test_backup_prune_preserves_foreign_and_incomplete_dirs(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    write(home_dir / ".claude/CLAUDE.md", "old instructions\n")
    backup_root = home_dir / ".ai-config-backup"
    write(backup_root / "foreign/keep.txt", "foreign\n")
    write(backup_root / "2020-01-01-000000000/keep.txt", "incomplete\n")
    for index in range(6):
        snapshot = backup_root / f"2020-01-02-00000{index}000"
        write(snapshot / ".ai-config-backup-owned", "ai-config-backup-v1\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (backup_root / "foreign/keep.txt").read_text() == "foreign\n"
    assert (backup_root / "2020-01-01-000000000/keep.txt").read_text() == "incomplete\n"
    completed = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and (path / ".ai-config-backup-owned").is_file()
    ]
    assert len(completed) == 5


def test_skill_supporting_scripts_and_agents_are_projected(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    skill = repo_dir / "claude/shared/both/demo"
    write(skill / "SKILL.md", "---\nname: demo\ndescription: Demo.\n---\n")
    write(skill / "scripts/run.py", "print('ok')\n")
    write(skill / "agents/reviewer.md", "review\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    projected = home_dir / ".agents/skills/demo"
    assert (projected / "scripts/run.py").is_file()
    assert (projected / "agents/reviewer.md").is_file()


def test_status_parses_quoted_crlf_mirror_metadata(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    write(home_dir / ".codex/config.toml", 'model = "gpt-5"\n')
    source = home_dir / "source one.md"
    write(source, "matching source\n")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest().upper()
    skill = repo_dir / "claude/shared/both/demo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(
        b"---\r\n"
        b"name: demo\r\n"
        b"description: Demo.\r\n"
        b"metadata:\r\n"
        b'  mirror-of: "~/source one.md"\r\n'
        + f'  mirror-hash: "{source_hash}"\r\n'.encode()
        + b"---\r\nBody.\r\n"
    )

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "All 1 mirrored shared skills up to date" in result.stdout


def test_status_codex_normalizes_crlf_on_both_sides(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    repo_config = repo_dir / "codex/config.toml"
    repo_config.parent.mkdir(parents=True)
    repo_config.write_bytes(b'model = "same"\r\n\r\n[features]\r\nsearch = true\r\n')
    live_config = home_dir / ".codex/config.toml"
    live_config.parent.mkdir(parents=True)
    live_config.write_bytes(
        b'model = "same"\r\n\r\n'
        b'[projects."C:/local"]\r\ntrust_level = "trusted"\r\n\r\n'
        b'[features]\r\nsearch = true\r\n'
    )

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No differences found" in result.stdout
    assert "~ config.toml" not in result.stdout


def test_init_codex_excludes_machine_local_notify(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    (repo_dir / "claude").mkdir()
    write(
        home_dir / ".codex/config.toml",
        'model = "gpt-5"\n'
        'notify = ["C:/runtime/codex-computer-use.exe", "turn-ended"]\n\n'
        "[features]\nsearch = true\n",
    )

    result = run_ai_config(repo_dir, home_dir, "init", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (repo_dir / "codex/config.toml").read_text() == (
        'model = "gpt-5"\n\n[features]\nsearch = true\n'
    )


def test_status_codex_ignores_machine_local_notify(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "codex/config.toml",
        'model = "gpt-5"\n\n[features]\nsearch = true\n',
    )
    write(
        home_dir / ".codex/config.toml",
        'model = "gpt-5"\n'
        'notify = ["/opt/codex-computer-use", "turn-ended"]\n\n'
        "[features]\nsearch = true\n",
    )

    result = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No differences found" in result.stdout
    assert "~ config.toml" not in result.stdout


def test_apply_codex_preserves_live_notify_and_projects(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "codex/config.toml",
        'model = "shared"\n'
        'notify = ["C:/stale/runtime.exe", "turn-ended"]\n\n'
        "[features]\nsearch = true\n",
    )
    write(
        home_dir / ".codex/config.toml",
        'model = "local"\n'
        'notify = ["/opt/live/runtime", "turn-ended"]\n\n'
        '[projects."/srv/work"]\ntrust_level = "trusted"\n',
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    config = (home_dir / ".codex/config.toml").read_text()
    assert 'model = "shared"' in config
    assert 'notify = ["/opt/live/runtime", "turn-ended"]' in config
    assert "C:/stale/runtime.exe" not in config
    assert '[projects."/srv/work"]' in config
    assert 'trust_level = "trusted"' in config


def test_apply_codex_filters_repo_notify_from_fresh_copy(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(
        repo_dir / "codex/config.toml",
        'model = "shared"\n'
        'notify = ["C:/stale/runtime.exe", "turn-ended"]\n',
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (home_dir / ".codex/config.toml").read_text() == 'model = "shared"\n'


def test_apply_rejects_symlink_destination_before_backup(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    external = tmp_path / "external.md"
    write(external, "external\n")
    live = home_dir / ".claude/CLAUDE.md"
    live.parent.mkdir(parents=True)
    live.symlink_to(external)

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 1
    assert "Refusing reparse point" in result.stderr + result.stdout
    assert external.read_text() == "external\n"
    assert not (home_dir / ".ai-config-backup").exists()


def test_apply_rejects_symlink_source_before_live_mutation(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    external = tmp_path / "external-rule.md"
    write(external, "external\n")
    rule = repo_dir / "claude/rules/external.md"
    rule.parent.mkdir(parents=True)
    rule.symlink_to(external)
    write(home_dir / ".claude/CLAUDE.md", "old instructions\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 1
    assert "Refusing reparse point" in result.stderr + result.stdout
    assert (home_dir / ".claude/CLAUDE.md").read_text() == "old instructions\n"
    assert not (home_dir / ".ai-config-backup").exists()


def test_manifest_traversal_cannot_prune_outside_skills(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    write(
        repo_dir / "claude/shared/both/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo.\n---\n",
    )
    skills = home_dir / ".agents/skills"
    write(skills / ".ai-config-managed", "../../outside\n")
    outside = home_dir / "outside/keep.txt"
    write(outside, "keep\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Ignoring unsafe managed skill name" in result.stdout
    assert outside.read_text() == "keep\n"


def test_large_skills_keep_single_frontmatter_and_remain_idempotent(
    tmp_path: Path,
) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    large_body = "x" * 70_000
    write(
        repo_dir / "claude/shared/both/existing/SKILL.md",
        "---\nname: existing\ndescription: Existing.\n---\n" + large_body + "\n",
    )
    write(
        repo_dir / "claude/shared/both/generated/SKILL.md",
        "# Generated\n\n" + large_body + "\n",
    )

    first = run_ai_config(repo_dir, home_dir, "apply", "codex")
    second = run_ai_config(repo_dir, home_dir, "apply", "codex")
    status = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert first.returncode == 0, first.stderr + first.stdout
    assert second.returncode == 0, second.stderr + second.stdout
    assert status.returncode == 0, status.stderr + status.stdout
    assert "No differences found" in status.stdout
    for name in ("existing", "generated"):
        content = (home_dir / f".agents/skills/{name}/SKILL.md").read_text()
        assert content.splitlines().count("---") == 2
        assert large_body in content


def test_windows_path_identity_normalizes_junction_namespace_prefixes() -> None:
    code = r"""
from ai_config.links import _normalize_windows_path_text

assert _normalize_windows_path_text(r"\\?\C:\Users\demo\rules") == (
    _normalize_windows_path_text(r"C:\Users\demo\rules")
)
assert _normalize_windows_path_text(r"\\?\UNC\server\share\skills") == (
    _normalize_windows_path_text(r"\\server\share\skills")
)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_mirror_deletion_failure_is_reported_with_backup(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "new instructions\n")
    write(repo_dir / "claude/rules/current.md", "current\n")
    write(home_dir / ".claude/CLAUDE.md", "old instructions\n")
    write(home_dir / ".claude/rules/stale/file.md", "stale\n")
    fsops = repo_dir / "ai_config/fsops.py"
    source = fsops.read_text(encoding="utf-8")
    anchor = "                shutil.rmtree(item)\n"
    assert source.count(anchor) == 1
    fsops.write_text(
        source.replace(
            anchor,
            "                raise RuntimeError('Injected mirror deletion failure')\n",
        ),
        encoding="utf-8",
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 1
    output = result.stderr + result.stdout
    assert "Injected mirror deletion failure" in output
    assert "Restore from backup if needed" in output
    snapshots = [
        path
        for path in (home_dir / ".ai-config-backup").iterdir()
        if (path / ".ai-config-backup-owned").is_file()
    ]
    assert len(snapshots) == 1
    assert (
        snapshots[0] / "claude/rules/stale/file.md"
    ).read_text() == "stale\n"


def test_status_previews_exact_mirror_deletions(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/rules/current.md", "current\n")
    write(home_dir / ".claude/rules/current.md", "current\n")
    write(home_dir / ".claude/rules/stale.md", "stale\n")
    write(home_dir / ".claude/rules/.credentials.json", "live secret\n")

    status = run_ai_config(repo_dir, home_dir, "status", "claude")

    assert status.returncode == 0, status.stderr + status.stdout
    assert "rules/stale.md" in status.stdout
    assert "only in live; apply removes" in status.stdout
    assert ".credentials.json" not in status.stdout
    assert "No differences found" not in status.stdout


def test_status_previews_only_managed_skill_deletions(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    for name in ("current", "removed"):
        write(
            repo_dir / f"codex/skills/{name}/SKILL.md",
            f"---\nname: {name}\ndescription: Test.\n---\n",
        )
    first = run_ai_config(repo_dir, home_dir, "apply", "codex")
    assert first.returncode == 0, first.stderr + first.stdout
    skills = home_dir / ".agents/skills"
    (repo_dir / "codex/skills/removed/SKILL.md").unlink()
    (repo_dir / "codex/skills/removed").rmdir()
    write(skills / "current/stale.md", "stale\n")
    write(skills / "current/.credentials.json", "live secret\n")
    write(skills / "hand-installed/SKILL.md", "hand installed\n")

    status = run_ai_config(repo_dir, home_dir, "status", "codex")

    assert status.returncode == 0, status.stderr + status.stdout
    assert "skills/current/stale.md" in status.stdout
    assert "skills/removed" in status.stdout
    assert "skills/hand-installed" not in status.stdout
    assert ".credentials.json" not in status.stdout


def test_init_all_preflights_every_tool_before_repo_mutation(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")

    result = run_ai_config(repo_dir, home_dir, "init", "all")

    assert result.returncode == 1
    assert "Codex config directory not found" in result.stderr + result.stdout
    assert (
        repo_dir / "claude/CLAUDE.md"
    ).read_text() == "repo instructions\n"


def test_status_reports_repo_and_live_mtime_order(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    repo_file = repo_dir / "claude/CLAUDE.md"
    live_file = home_dir / ".claude/CLAUDE.md"
    write(repo_file, "repo instructions\n")
    write(live_file, "live instructions\n")
    os.utime(repo_file, (1_700_000_000, 1_700_000_000))
    os.utime(live_file, (1_800_000_000, 1_800_000_000))

    result = run_ai_config(repo_dir, home_dir, "status", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "mtime hint: live newer" in result.stdout
    assert "repo 2023-" in result.stdout
    assert "live 2027-" in result.stdout


def test_apply_preserves_source_mtime_for_generated_skill(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "codex/config.toml", 'model = "test"\n')
    agent = repo_dir / "claude/agents/demo.md"
    write(agent, "# Demo\n\nBody.\n")
    expected_mtime_ns = 1_700_000_000_123_456_789
    os.utime(agent, ns=(expected_mtime_ns, expected_mtime_ns))
    source_mtime_ns = agent.stat().st_mtime_ns

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    projected = home_dir / ".agents/skills/demo/SKILL.md"
    assert projected.stat().st_mtime_ns == source_mtime_ns


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_apply_codex_accepts_direct_shared_agents_link(
    tmp_path: Path,
    relative: bool,
) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    target = home_dir / ".claude/CLAUDE.md"
    write(target, "old instructions\n")
    agents = home_dir / ".codex/AGENTS.md"
    agents.parent.mkdir(parents=True)
    link_target = Path("../.claude/CLAUDE.md") if relative else target
    agents.symlink_to(link_target)

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    assert agents.is_symlink()
    assert target.read_text() == "repo instructions\n"


@pytest.mark.parametrize("kind", ["foreign", "broken", "chained"])
def test_apply_codex_rejects_unsafe_shared_agents_link(
    tmp_path: Path,
    kind: str,
) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    agents = home_dir / ".codex/AGENTS.md"
    agents.parent.mkdir(parents=True)
    external = tmp_path / "external-instructions.md"
    write(external, "external instructions\n")

    if kind == "foreign":
        agents.symlink_to(external)
    elif kind == "broken":
        agents.symlink_to(home_dir / ".claude/CLAUDE.md")
    else:
        shared_target = home_dir / ".claude/CLAUDE.md"
        shared_target.parent.mkdir(parents=True)
        shared_target.symlink_to(external)
        agents.symlink_to(shared_target)

    result = run_ai_config(repo_dir, home_dir, "apply", "codex")

    assert result.returncode == 1
    assert "Failed to apply config" in result.stderr + result.stdout
    assert external.read_text() == "external instructions\n"
    assert not (home_dir / ".ai-config-backup").exists()


def test_apply_agy_backs_up_contained_plugin_symlinks(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "agy/settings.json", '{"theme":"new"}\n')
    write(home_dir / ".gemini/antigravity-cli/settings.json", '{"theme":"old"}\n')
    plugins = home_dir / ".gemini/antigravity-cli/plugins"
    write(plugins / "node_modules/package/bin.js", "bin\n")
    executable = plugins / "node_modules/.bin/tool"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(Path("../package/bin.js"))
    write(plugins / "src/data/item.txt", "data\n")
    data_link = plugins / "extension/data"
    data_link.parent.mkdir(parents=True)
    data_link.symlink_to(Path("../src/data"), target_is_directory=True)
    repo_plugins = repo_dir / "claude/plugins"
    write(repo_plugins / "node_modules/package/bin.js", "bin\n")
    repo_executable = repo_plugins / "node_modules/.bin/tool"
    repo_executable.parent.mkdir(parents=True)
    repo_executable.symlink_to(Path("../package/bin.js"))
    write(repo_plugins / "src/data/item.txt", "data\n")
    repo_data_link = repo_plugins / "extension/data"
    repo_data_link.parent.mkdir(parents=True)
    repo_data_link.symlink_to(Path("../src/data"), target_is_directory=True)

    result = run_ai_config(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert executable.is_symlink()
    assert data_link.is_symlink()
    snapshots = sorted((home_dir / ".ai-config-backup").iterdir())
    backup_plugins = snapshots[-1] / "agy/plugins"
    assert (backup_plugins / "node_modules/.bin/tool").is_symlink()
    assert (backup_plugins / "extension/data").is_symlink()


@pytest.mark.parametrize("kind", ["relative", "absolute", "chained"])
def test_apply_agy_rejects_plugin_symlink_escape_before_mutation(
    tmp_path: Path,
    kind: str,
) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "agy/settings.json", '{"theme":"new"}\n')
    settings = home_dir / ".gemini/antigravity-cli/settings.json"
    write(settings, '{"theme":"old"}\n')
    plugins = home_dir / ".gemini/antigravity-cli/plugins"
    plugins.mkdir(parents=True)
    write(repo_dir / "claude/plugins/managed.txt", "managed\n")
    outside = home_dir / ".gemini/antigravity-cli/outside.txt"
    write(outside, "outside\n")

    if kind == "relative":
        (plugins / "escape").symlink_to(Path("../outside.txt"))
    elif kind == "absolute":
        (plugins / "escape").symlink_to(outside)
    else:
        (plugins / "inner").symlink_to(Path("../outside.txt"))
        (plugins / "escape").symlink_to(Path("inner"))

    result = run_ai_config(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 1
    assert "symlink" in (result.stderr + result.stdout).lower()
    assert settings.read_text() == '{"theme":"old"}\n'
    assert outside.read_text() == "outside\n"
    assert not (home_dir / ".ai-config-backup").exists()


def test_apply_agy_rejects_source_plugin_symlink_escape(tmp_path: Path) -> None:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "agy/settings.json", '{"theme":"new"}\n')
    outside = tmp_path / "outside-plugin.txt"
    write(outside, "outside\n")
    plugins = repo_dir / "claude/plugins"
    plugins.mkdir(parents=True)
    (plugins / "escape").symlink_to(outside)
    settings = home_dir / ".gemini/antigravity-cli/settings.json"
    write(settings, '{"theme":"old"}\n')

    result = run_ai_config(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 1
    assert "symlink" in (result.stderr + result.stdout).lower()
    assert settings.read_text() == '{"theme":"old"}\n'
    assert outside.read_text() == "outside\n"
    assert not (home_dir / ".ai-config-backup").exists()
