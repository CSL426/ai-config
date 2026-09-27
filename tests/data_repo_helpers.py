"""Temporary data repositories and CLI runs shared by the sync and push tests."""

import os
import subprocess
import sys
from pathlib import Path

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


def create_data_remote(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True)
    configure_git_identity(seed)
    commit_and_push_settings(seed, "{}", "initial")
    branch = run_git(seed, "branch", "--show-current")
    run_git(seed, "branch", "--set-upstream-to", f"origin/{branch}")
    return remote, seed


def run_data_cli(
    data_repo: Path,
    home: Path,
    *args: str,
    input_text: "str | None" = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(data_repo)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "ai_config", *args],
        capture_output=True,
        text=True,
        input=input_text,
        env=env,
        check=False,
    )


def run_data_alias_cli(
    data_repo: Path,
    home: Path,
    *args: str,
    input_text: "str | None" = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(data_repo)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    launcher = (
        "import sys\n"
        "from ai_config.cli import console_main\n"
        "sys.argv = ['acg', *sys.argv[1:]]\n"
        "raise SystemExit(console_main())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", launcher, *args],
        capture_output=True,
        text=True,
        input=input_text,
        env=env,
        check=False,
    )
