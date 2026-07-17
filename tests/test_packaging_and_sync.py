import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ai_config.cli import console_main

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def configure_git_identity(repo: Path) -> None:
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "user.email", "test@example.com")


def commit_and_push_settings(repo: Path, content: str, message: str) -> None:
    settings = repo / "claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(content, encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", message)
    run_git(repo, "push", "origin", "HEAD")


def test_pyproject_toml_script_entry() -> None:
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.is_file(), "pyproject.toml must exist at repo root"

    with pyproject_path.open("rb") as file:
        data = tomllib.load(file)

    scripts = data.get("project", {}).get("scripts", {})
    assert scripts.get("ai-config") == "ai_config.cli:console_main"


def test_console_main_usage_entrypoint(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = os.environ.copy()
    environment.pop("AI_CONFIG_ENTRYPOINT", None)
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(sys, "argv", [sys.argv[0]])

    assert console_main() == 0

    captured = capsys.readouterr()
    assert "ai-config <command> [tool]" in captured.out


def test_ai_config_repo_env_var(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    (fake_repo / "claude").mkdir()

    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(fake_repo)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "list"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert "claude (0 files)" in result.stdout


def test_default_data_repo_is_nested_beneath_tool_checkout(tmp_path: Path) -> None:
    tool_root = tmp_path / "home" / "ai-config"
    shutil.copytree(REPO_ROOT / "ai_config", tool_root / "ai_config")
    data_repo = tool_root / "data"
    (data_repo / "claude").mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env.pop("AI_CONFIG_REPO", None)
    env["PYTHONPATH"] = str(tool_root)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ai_config.paths import SCRIPT_DIR; print(SCRIPT_DIR)",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=True,
    )

    assert Path(result.stdout.strip()) == data_repo.resolve()


def test_missing_claude_directory_fails(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()

    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env["AI_CONFIG_REPO"] = str(fake_repo)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "list"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "AI_CONFIG_REPO" in result.stderr or "AI_CONFIG_REPO" in result.stdout


def test_sync_subcommand(tmp_path: Path) -> None:
    non_git_dir = tmp_path / "non-git"
    non_git_dir.mkdir()
    (non_git_dir / "claude").mkdir()

    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(non_git_dir)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "sync"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0

    remote_dir = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True)

    clone_dir = tmp_path / "clone"
    subprocess.run(["git", "clone", str(remote_dir), str(clone_dir)], check=True)
    configure_git_identity(clone_dir)
    commit_and_push_settings(clone_dir, "{}", "initial")

    push_workspace = tmp_path / "push-ws"
    subprocess.run(["git", "clone", str(remote_dir), str(push_workspace)], check=True)
    configure_git_identity(push_workspace)
    commit_and_push_settings(push_workspace, '{"theme": "dark"}', "update remote")

    clone_head_before = run_git(clone_dir, "rev-parse", "HEAD")

    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(clone_dir)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "sync"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, f"sync failed: {result.stderr}\nstdout: {result.stdout}"

    clone_head_after = run_git(clone_dir, "rev-parse", "HEAD")

    assert clone_head_before != clone_head_after
    assert "Status:" in result.stdout
