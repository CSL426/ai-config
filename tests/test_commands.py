"""Behaviour tests for the commands the sync suite didn't cover yet:
reset, project, backup pruning, codex shared-home links, and apply
idempotency. Written as the Phase 0 freeze for the Python CLI migration."""

from pathlib import Path

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
