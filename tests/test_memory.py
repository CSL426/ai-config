"""Shared memory: one notebook in the data repo that every tool reads."""

import subprocess
from pathlib import Path

import pytest
from test_apply_projection import run_ai_config, write
from test_commands import make_full_repo

from ai_config import memory
from ai_config.commands import push
from ai_config.safety import is_reparse_point


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:owner/repo.git", "owner--repo"),
        ("https://github.com/owner/repo", "owner--repo"),
        ("ssh://git@code-host/team/sample.git", "team--sample"),
        ("/srv/git/team/tool.git", "team--tool"),
        ("https://host/own er/re po.git", "own-er--re-po"),
        ("nonsense", ""),
        ("", ""),
    ],
)
def test_parse_project_key(url: str, expected: str) -> None:
    assert memory.parse_project_key(url) == expected


def test_block_install_is_idempotent_and_reversible() -> None:
    original = "# My rules\n\nKeep it short.\n"
    once = memory.with_block(original)
    assert once.count(memory.BLOCK_BEGIN) == 1
    assert memory.with_block(once) == once
    assert memory.without_block(once) == original
    assert memory.without_block(memory.with_block("")) == ""


def test_project_key_falls_back_to_directory_name(tmp_path: Path) -> None:
    plain = tmp_path / "loose dir"
    plain.mkdir()
    key = memory.project_key(plain)
    assert key.key == "loose-dir"
    assert key.stable is False


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_enable_links_and_installs_rules_then_disable_restores(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    live_rules = home_dir / ".claude" / "CLAUDE.md"
    write(live_rules, "# live rules\n")

    result = run_ai_config(repo_dir, home_dir, "memory", "enable")
    assert result.returncode == 0, result.stdout + result.stderr

    link = home_dir / ".claude" / "shared-memory"
    assert is_reparse_point(link)
    assert link.resolve() == (repo_dir / "memory").resolve()
    assert (repo_dir / "memory" / "MEMORY.md").is_file()
    assert live_rules.read_text(encoding="utf-8").startswith("# live rules\n")
    assert memory.BLOCK_BEGIN in live_rules.read_text(encoding="utf-8")
    source_rules = repo_dir / "claude" / "CLAUDE.md"
    assert memory.BLOCK_BEGIN in source_rules.read_text(encoding="utf-8")
    agy_rules = home_dir / ".gemini" / "config" / "rules" / "acg-memory.md"
    assert memory.BLOCK_BEGIN in agy_rules.read_text(encoding="utf-8")

    # 第二次啟用不能疊出第二個區塊
    again = run_ai_config(repo_dir, home_dir, "memory", "enable")
    assert again.returncode == 0
    assert live_rules.read_text(encoding="utf-8").count(memory.BLOCK_BEGIN) == 1

    status = run_ai_config(repo_dir, home_dir, "memory", "status")
    assert status.returncode == 0
    assert "已安裝" in status.stdout
    assert "指向記憶目錄" in status.stdout

    disabled = run_ai_config(repo_dir, home_dir, "memory", "disable")
    assert disabled.returncode == 0, disabled.stdout + disabled.stderr
    assert not link.exists() and not is_reparse_point(link)
    assert live_rules.read_text(encoding="utf-8") == "# live rules\n"
    assert source_rules.read_text(encoding="utf-8") == "repo instructions\n"
    assert not agy_rules.exists()
    # 停用保留筆記
    assert (repo_dir / "memory" / "MEMORY.md").is_file()


def test_enable_refuses_when_link_path_is_a_real_directory(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    (home_dir / ".claude" / "shared-memory").mkdir(parents=True)
    result = run_ai_config(repo_dir, home_dir, "memory", "enable")
    assert result.returncode == 1
    assert "無法建立連結" in result.stderr


def test_status_never_writes(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    result = run_ai_config(repo_dir, home_dir, "memory", "status")
    assert result.returncode == 0
    assert not (repo_dir / "memory").exists()
    assert not (home_dir / ".claude" / "shared-memory").exists()


def test_push_scopes_treat_memory_as_its_own_range() -> None:
    assert push._push_scopes("memory") == ["memory"]
    assert push._push_scopes("claude") == ["claude"]
    assert push._push_scopes("all")[-1] == "memory"
    assert "memory" not in push._push_scopes("codex")


def test_only_memory_changes_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(push, "_working_paths", lambda: ["memory/MEMORY.md"])
    assert push._only_memory_changes() is True
    monkeypatch.setattr(
        push, "_working_paths", lambda: ["memory/x.md", "claude/CLAUDE.md"]
    )
    assert push._only_memory_changes() is False
    monkeypatch.setattr(push, "_working_paths", list)
    assert push._only_memory_changes() is False


def test_pull_blocked_by_unsaved_memory_points_at_memory_push(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(repo_dir / "memory" / "MEMORY.md", "# notes\n")
    assert _git(repo_dir, "init", "-q").returncode == 0
    assert _git(repo_dir, "add", "-A").returncode == 0
    assert _git(repo_dir, "commit", "-q", "-m", "base").returncode == 0
    write(repo_dir / "memory" / "MEMORY.md", "# notes\n- remembered\n")

    result = run_ai_config(repo_dir, home_dir, "pull")
    assert result.returncode == 1
    assert "memory push" in result.stdout


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/home/user/project", "-home-user-project"),
        ("/home/user/project/", "-home-user-project"),
        ("/tmp/中文 dir", "-tmp----dir"),
        ("/srv/a.b_c", "-srv-a-b-c"),
    ],
)
def test_session_slug_matches_claude_projects_naming(path: str, expected: str) -> None:
    assert memory.session_slug(Path(path)) == expected


def _run_in_project(repo_dir: Path, home_dir: Path, project: Path, *args: str):
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["AI_CONFIG_REPO"] = str(repo_dir)
    return subprocess.run(
        [sys.executable, "-m", "ai_config", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_adopt_moves_journal_into_project_memory_and_release_undoes(
    tmp_path: Path,
) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    # 隔離 HOME 裡假裝裝了 remember plugin
    (home_dir / ".claude/plugins/cache/claude-plugins-official/remember/0.25.0").mkdir(
        parents=True
    )
    write(home_dir / ".remember/config.json", '{"model": "sonnet"}\n')

    project = tmp_path / "proj"
    project.mkdir()
    assert _git(project, "init", "-q").returncode == 0
    assert (
        _git(project, "remote", "add", "origin", "git@github.com:o/r.git").returncode
        == 0
    )
    write(project / ".remember/recent.md", "# Recent\n- did things\n")
    write(project / ".remember/.gitignore", "*\n")
    write(project / ".remember/logs/x.log", "noise\n")

    result = _run_in_project(repo_dir, home_dir, project, "memory", "adopt")
    assert result.returncode == 0, result.stdout + result.stderr

    target = repo_dir / "memory/projects/o--r/journal"
    assert (home_dir / ".claude/shared-memory").resolve() == repo_dir / "memory"
    assert (target / "recent.md").read_text(
        encoding="utf-8"
    ) == "# Recent\n- did things\n"
    assert (target / "logs/x.log").is_file()
    assert "logs/" in (target / ".gitignore").read_text(encoding="utf-8")
    assert not (target / "logs" / ".gitignore").exists()
    link = repo_dir / "memory/journal" / memory.session_slug(project)
    assert is_reparse_point(link) and link.resolve() == target.resolve()
    assert (project / ".remember/MIGRATED-TO.txt").is_file()
    assert not (project / ".remember/recent.md").exists()
    assert "journal/" in (repo_dir / "memory/.gitignore").read_text(encoding="utf-8")
    # 使用者全域設定:加了 data_dir,原本的鍵保留
    config = (home_dir / ".remember/config.json").read_text(encoding="utf-8")
    assert memory.JOURNAL_TEMPLATE in config and '"model": "sonnet"' in config

    again = _run_in_project(repo_dir, home_dir, project, "memory", "adopt")
    assert again.returncode == 0 and "已經在共用記憶裡" in again.stdout

    released = _run_in_project(repo_dir, home_dir, project, "memory", "release")
    assert released.returncode == 0, released.stdout + released.stderr
    assert not is_reparse_point(link) and link.is_dir()
    assert (link / "recent.md").is_file()
    assert not (target / "recent.md").exists()


def test_adopt_refuses_to_override_a_custom_data_dir(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    (home_dir / ".claude/plugins/cache/claude-plugins-official/remember/0.25.0").mkdir(
        parents=True
    )
    write(home_dir / ".remember/config.json", '{"data_dir": "~/elsewhere/{slug}"}\n')
    project = tmp_path / "proj"
    project.mkdir()
    result = _run_in_project(repo_dir, home_dir, project, "memory", "adopt")
    assert result.returncode == 1
    assert "elsewhere" in result.stderr
    assert '"data_dir": "~/elsewhere/{slug}"' in (
        home_dir / ".remember/config.json"
    ).read_text(encoding="utf-8")


def test_disable_removes_only_our_journal_setting(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    (home_dir / ".claude/plugins/cache/claude-plugins-official/remember/0.25.0").mkdir(
        parents=True
    )
    assert run_ai_config(repo_dir, home_dir, "memory", "enable").returncode == 0
    config_path = home_dir / ".remember/config.json"
    assert memory.JOURNAL_TEMPLATE in config_path.read_text(encoding="utf-8")
    assert run_ai_config(repo_dir, home_dir, "memory", "disable").returncode == 0
    assert not config_path.exists()


def test_rules_block_is_identical_on_every_machine() -> None:
    # 區塊會寫進同步的 CLAUDE.md;嵌入本機的執行檔名稱會讓每台機器互相改寫
    assert "acg memory path" in memory.RULES_BLOCK
    assert "ai-config.sh" not in memory.RULES_BLOCK
    assert "{" not in memory.RULES_BLOCK


def test_home_remember_config_dir_is_not_a_legacy_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".remember").mkdir(parents=True)
    (home / ".remember" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(memory, "HOME", home)
    assert memory.legacy_journal_dir(home) is None
    assert memory.journal_state(home)[0] == "none"
    project = tmp_path / "proj"
    (project / ".remember").mkdir(parents=True)
    assert memory.legacy_journal_dir(project) == project / ".remember"
