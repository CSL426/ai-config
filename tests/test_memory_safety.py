"""Memory mutations must preserve unrelated files and recover on failure."""

import json
from pathlib import Path

import pytest
from test_apply_projection import run_ai_config, write
from test_commands import make_full_repo

from ai_config import memory
from ai_config.commands import memory as command


def symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("Native symlinks unavailable")


@pytest.mark.parametrize("name", ["MEMORY.md", ".gitignore", "topics"])
def test_enable_rejects_memory_links_before_any_mutation(tmp_path, name):
    repo, home = make_full_repo(tmp_path)
    external = tmp_path / "external"
    if name == ".gitignore":
        external.write_text("external data\n")
    elif name == "topics":
        external.mkdir()
    symlink(repo / "memory" / name, external, directory=name == "topics")
    source = (repo / "claude/CLAUDE.md").read_bytes()
    result = run_ai_config(repo, home, "memory", "enable")
    assert result.returncode == 1
    assert "reparse" in result.stderr
    assert (repo / "claude/CLAUDE.md").read_bytes() == source
    assert not (home / ".claude/shared-memory").exists()
    assert not (home / ".ai-config-backup").exists()
    if name == "MEMORY.md":
        assert not external.exists()
    elif name == ".gitignore":
        assert external.read_text() == "external data\n"


def test_foreign_agy_rules_refused_before_creating_memory(tmp_path):
    repo, home = make_full_repo(tmp_path)
    rule = home / ".gemini/config/rules/acg-memory.md"
    write(rule, "My independent rules\n")
    result = run_ai_config(repo, home, "memory", "enable")
    assert result.returncode == 1
    assert "unmanaged agy" in result.stderr
    assert rule.read_text() == "My independent rules\n"
    assert not (repo / "memory").exists()


def test_backup_keeps_all_rule_versions_and_unique_snapshots(tmp_path):
    repo, home = make_full_repo(tmp_path)
    live = home / ".claude/CLAUDE.md"
    source = repo / "claude/CLAUDE.md"
    codex = home / ".codex/AGENTS.md"
    agy = home / ".gemini/config/rules/acg-memory.md"
    write(live, "Unique live rules\n")
    write(codex, "Unique Codex rules\n")
    write(agy, memory.RULES_BLOCK + "Custom agy text\n")
    originals = {p: p.read_bytes() for p in (live, source, codex, agy)}
    assert run_ai_config(repo, home, "memory", "enable").returncode == 0
    snapshots = list((home / ".ai-config-backup").glob("memory-*/manifest.json"))
    assert len(snapshots) == 1
    manifest = json.loads(snapshots[0].read_text())
    saved = {Path(original): (snapshots[0].parent / key).read_bytes()
             for key, original in manifest.items()}
    assert all(saved[path] == content for path, content in originals.items())
    assert run_ai_config(repo, home, "memory", "enable").returncode == 0
    assert len(list((home / ".ai-config-backup").glob("memory-*/manifest.json"))) == 2
    assert run_ai_config(repo, home, "memory", "disable").returncode == 0
    assert "Custom agy text" in agy.read_text()


@pytest.mark.parametrize("shared", [False, True])
def test_codex_entry_installed_and_removed_without_losing_rules(tmp_path, shared):
    repo, home = make_full_repo(tmp_path)
    live = home / ".claude/CLAUDE.md"
    codex = home / ".codex/AGENTS.md"
    write(live, "Claude original\n")
    if shared:
        symlink(codex, live)
    else:
        write(codex, "Codex original\n")
    source = repo / "codex/AGENTS.md"
    write(source, "Codex source\n")
    assert run_ai_config(repo, home, "memory", "enable").returncode == 0
    assert memory.BLOCK_BEGIN in codex.read_text()
    assert memory.BLOCK_BEGIN in source.read_text()
    status = run_ai_config(repo, home, "memory", "status")
    assert "Codex 規則已安裝" in status.stdout
    assert run_ai_config(repo, home, "memory", "disable").returncode == 0
    assert codex.read_text() == ("Claude original\n" if shared else "Codex original\n")
    assert source.read_text() == "Codex source\n"
    assert codex.is_symlink() == shared


def test_foreign_codex_link_is_not_followed(tmp_path):
    repo, home = make_full_repo(tmp_path)
    external = tmp_path / "external.md"
    write(external, "Private external rules\n")
    symlink(home / ".codex/AGENTS.md", external)
    result = run_ai_config(repo, home, "memory", "enable")
    assert result.returncode == 1
    assert external.read_text() == "Private external rules\n"
    assert not (repo / "memory").exists()


@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "data"
    home.mkdir()
    repo.mkdir()
    for name, value in {
        "SCRIPT_DIR": repo, "HOME": home, "CLAUDE_HOME": home / ".claude",
        "CODEX_HOME": home / ".codex",
        "MEMORY_LINK": home / ".claude/shared-memory",
        "AGY_CONFIG_RULES": home / ".gemini/config/rules",
        "REMEMBER_USER_CONFIG": home / ".remember/config.json",
    }.items():
        monkeypatch.setattr(memory, name, value)
    monkeypatch.setattr(memory, "claude_source_dir", lambda: repo / "claude")
    monkeypatch.setattr(command, "MEMORY_LINK", memory.MEMORY_LINK)
    backup = home / ".ai-config-backup"
    monkeypatch.setattr(command, "BACKUP_BASE", backup)
    from ai_config import locking
    monkeypatch.setattr(locking, "BACKUP_BASE", backup)
    return repo, home


def test_enable_failure_restores_rules_and_removes_new_link(isolated_memory, monkeypatch):
    repo, home = isolated_memory
    live = home / ".claude/CLAUDE.md"
    write(live, "Original rules\n")
    write(repo / "claude/CLAUDE.md", "Source\n")

    def fail():
        raise OSError("injected failure")

    monkeypatch.setattr(memory, "install_agy_rules", fail)
    assert command.run_memory(["enable"]) == 1
    assert live.read_text() == "Original rules\n"
    assert (repo / "claude/CLAUDE.md").read_text() == "Source\n"
    assert not memory.MEMORY_LINK.exists()
    assert not memory.index_path().exists()


def test_invalid_remember_json_preserved(tmp_path):
    repo, home = make_full_repo(tmp_path)
    config = home / ".remember/config.json"
    write(config, "{invalid json")
    result = run_ai_config(repo, home, "memory", "enable")
    assert result.returncode == 1
    assert config.read_text() == "{invalid json"
    assert not (repo / "memory").exists()
