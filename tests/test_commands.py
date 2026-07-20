"""Behaviour tests for the commands the sync suite didn't cover yet:
reset, project, backup pruning, codex shared-home links, and apply
idempotency. Written as the Phase 0 freeze for the Python CLI migration."""

import json
import os
from pathlib import Path

import pytest

from test_apply_projection import IMPL, copy_runtime_files, run_ai_config, write


def make_full_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo"
    home_dir = tmp_path / "home"
    repo_dir.mkdir()
    home_dir.mkdir()
    copy_runtime_files(repo_dir)
    write(repo_dir / "claude/CLAUDE.md", "repo instructions\n")
    write(repo_dir / "claude/settings.json", '{"enabledPlugins": {}}\n')
    write(repo_dir / "codex/config.toml", 'model = "gpt-5"\n')
    write(repo_dir / "agy/settings.json", '{"theme": "neon"}\n')
    return repo_dir, home_dir


# ─── reset ────────────────────────────────────────────────────


def test_reset_confirmed_clears_files_but_keeps_dirs(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)

    result = run_ai_config(repo_dir, home_dir, "reset", input_text="y\n")

    assert result.returncode == 0, result.stderr + result.stdout
    assert not (repo_dir / "claude/CLAUDE.md").exists()
    assert not (repo_dir / "codex/config.toml").exists()
    assert not (repo_dir / "agy/settings.json").exists()
    assert (repo_dir / "claude").is_dir()
    assert (repo_dir / "codex").is_dir()
    # Repo runtime itself must survive reset
    runtime_dir = "ai_config" if IMPL == "py" else "scripts"
    assert (repo_dir / runtime_dir).is_dir()


def test_reset_declined_keeps_everything(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)

    result = run_ai_config(repo_dir, home_dir, "reset", input_text="n\n")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (repo_dir / "claude/CLAUDE.md").exists()
    assert (repo_dir / "codex/config.toml").exists()


# ─── project ──────────────────────────────────────────────────


def test_project_codex_uses_live_claude_not_repo(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")

    result = run_ai_config(repo_dir, home_dir, "project", "codex")

    assert result.returncode == 0, result.stderr + result.stdout
    agents = (home_dir / ".codex/AGENTS.md").read_text(encoding="utf-8")
    assert agents == "live instructions\n"


def test_project_never_targets_claude_itself(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")

    result = run_ai_config(repo_dir, home_dir, "project", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No tools projected" in result.stdout


# ─── backup & prune ───────────────────────────────────────────


def test_apply_backs_up_existing_files_and_prunes_to_five(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "old live instructions\n")
    backup_base = home_dir / ".ai-config-backup"
    for i in range(7):
        snapshot = backup_base / f"2020-01-01-00000{i}000"
        (snapshot / "claude").mkdir(parents=True)
        write(snapshot / ".ai-config-backup-owned", "ai-config-backup-v1\n")

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    snapshots = sorted(p.name for p in backup_base.iterdir() if p.is_dir())
    assert len(snapshots) == 5, snapshots
    # The newest snapshot is the one just created and holds the old live file
    newest = backup_base / snapshots[-1]
    assert not newest.name.startswith("2020-")
    backed_up = newest / "claude" / "CLAUDE.md"
    assert backed_up.read_text(encoding="utf-8") == "old live instructions\n"
    # And the live file was replaced by the repo version
    live = (home_dir / ".claude/CLAUDE.md").read_text(encoding="utf-8")
    assert live == "repo instructions\n"


# ─── idempotency ──────────────────────────────────────────────


def test_status_is_clean_immediately_after_apply(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)

    apply_result = run_ai_config(repo_dir, home_dir, "apply", "all")
    assert apply_result.returncode == 0, apply_result.stderr + apply_result.stdout

    status_result = run_ai_config(repo_dir, home_dir, "status")
    assert status_result.returncode == 0, status_result.stderr + status_result.stdout
    assert status_result.stdout.count("No differences found") == 3, status_result.stdout
    assert "only in ai-config" not in status_result.stdout


def test_init_agy_excludes_trusted_workspaces(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        home_dir / ".gemini/antigravity-cli/settings.json",
        json.dumps(
            {
                "model": "live",
                "trustedWorkspaces": [r"G:\我的雲端硬碟\Personal\Resume"],
            },
            ensure_ascii=False,
        ),
    )

    result = run_ai_config(repo_dir, home_dir, "init", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads((repo_dir / "agy/settings.json").read_text()) == {
        "model": "live"
    }


def test_apply_agy_preserves_live_trusted_workspaces(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        repo_dir / "agy/settings.json",
        '{"model":"repo","trustedWorkspaces":["/repo/path"]}\n',
    )
    live_settings = home_dir / ".gemini/antigravity-cli/settings.json"
    write(
        live_settings,
        '{"model":"live","trustedWorkspaces":["G:\\\\local"]}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(live_settings.read_text()) == {
        "model": "repo",
        "trustedWorkspaces": [r"G:\local"],
    }


def test_apply_agy_fresh_copy_excludes_repo_trusted_workspaces(
    tmp_path: Path,
) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        repo_dir / "agy/settings.json",
        '{"model":"repo","trustedWorkspaces":["/repo/path"]}\n',
    )
    live_settings = home_dir / ".gemini/antigravity-cli/settings.json"

    result = run_ai_config(repo_dir, home_dir, "apply", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(live_settings.read_text()) == {"model": "repo"}


def test_status_agy_ignores_trusted_workspaces(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        repo_dir / "agy/settings.json",
        '{"model":"same","trustedWorkspaces":["/repo/path"]}\n',
    )
    write(
        home_dir / ".gemini/antigravity-cli/settings.json",
        '{"model":"same","trustedWorkspaces":["G:\\\\local"]}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "status", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No differences found" in result.stdout
    assert "~ settings.json" not in result.stdout


def test_init_agy_excludes_permissions(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        home_dir / ".gemini/antigravity-cli/settings.json",
        '{"model":"live","permissions":{"allow":["command(ls)"]}}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "init", "agy")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads((repo_dir / "agy/settings.json").read_text()) == {
        "model": "live"
    }


def test_init_claude_excludes_permissions(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "live instructions\n")
    write(
        home_dir / ".claude/settings.json",
        '{"model":"live","permissions":{"allow":["Bash(rsync -a *)"]}}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "init", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads((repo_dir / "claude/settings.json").read_text()) == {
        "model": "live"
    }


def test_apply_claude_preserves_live_permissions(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        repo_dir / "claude/settings.json",
        '{"model":"repo","permissions":{"allow":["/repo/rule"]}}\n',
    )
    live_settings = home_dir / ".claude/settings.json"
    write(
        live_settings,
        '{"model":"live","permissions":{"allow":["Bash(ls)"]}}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(live_settings.read_text()) == {
        "model": "repo",
        "permissions": {"allow": ["Bash(ls)"]},
    }


def test_apply_claude_fresh_copy_excludes_repo_permissions(
    tmp_path: Path,
) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        repo_dir / "claude/settings.json",
        '{"model":"repo","permissions":{"allow":["/repo/rule"]}}\n',
    )
    live_settings = home_dir / ".claude/settings.json"

    result = run_ai_config(repo_dir, home_dir, "apply", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(live_settings.read_text()) == {"model": "repo"}


def test_status_claude_ignores_permissions(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "repo instructions\n")
    write(
        repo_dir / "claude/settings.json",
        '{"model":"same","permissions":{"allow":["/repo/rule"]}}\n',
    )
    write(
        home_dir / ".claude/settings.json",
        '{"model":"same","permissions":{"allow":["Bash(ls)"]}}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "status", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No differences found" in result.stdout
    assert "~ settings.json" not in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="Unix symlink contract")
def test_status_agy_rejects_repo_settings_symlink_before_read(
    tmp_path: Path,
) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    settings = repo_dir / "agy/settings.json"
    settings.unlink()
    external = tmp_path / "external-settings.json"
    write(external, '{"model":"external"}\n')
    settings.symlink_to(external)
    write(
        home_dir / ".gemini/antigravity-cli/settings.json",
        '{"model":"live"}\n',
    )

    result = run_ai_config(repo_dir, home_dir, "status", "agy")

    assert result.returncode == 1
    assert "reparse point source file" in result.stderr + result.stdout
    assert external.read_text() == '{"model":"external"}\n'
