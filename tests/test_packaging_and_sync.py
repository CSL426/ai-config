import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ai_config import setup as setup_cli
from ai_config.cli import console_main
from ai_config.config import save_data_repo

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
    assert scripts.get("acg") == "ai_config.cli:console_main"


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
    assert "setup" in captured.out


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


def test_setup_clones_verifies_push_and_persists_data_repo(tmp_path: Path) -> None:
    remote, _ = create_data_remote(tmp_path)
    data_repo = tmp_path / "設定資料"
    data_repo.mkdir()
    config = tmp_path / "config" / "config.json"
    refs_before = run_git(remote, "show-ref")

    env = os.environ.copy()
    env["AI_CONFIG_CONFIG"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_config",
            "setup",
            "--data-dir",
            str(data_repo),
            "--repo-url",
            str(remote),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "data_repo": str(data_repo.resolve())
    }
    assert run_git(remote, "show-ref") == refs_before
    assert "temporary ref was removed" in result.stdout

    resolved = subprocess.run(
        [sys.executable, "-m", "ai_config", "list"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr + resolved.stdout
    assert "claude (1 files)" in resolved.stdout


def test_interactive_setup_defaults_to_configured_data_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_repo = tmp_path / "configured-data"
    config = tmp_path / "config.json"
    save_data_repo(data_repo, config)
    prompt_defaults = []

    monkeypatch.setenv("AI_CONFIG_CONFIG", str(config))
    monkeypatch.delenv("AI_CONFIG_REPO", raising=False)
    monkeypatch.setattr(setup_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        setup_cli,
        "_prompt",
        lambda _label, default=None: prompt_defaults.append(default) or default or "",
    )
    monkeypatch.setattr(setup_cli, "_has_usable_remote", lambda *_args: True)
    monkeypatch.setattr(
        setup_cli,
        "setup_repository",
        lambda data_dir, **_kwargs: data_dir,
    )

    assert setup_cli.run_setup([]) == 0
    assert prompt_defaults == [str(data_repo.resolve())]


def test_setup_failure_does_not_save_config_or_keep_new_remote(tmp_path: Path) -> None:
    data_repo = tmp_path / "data"
    data_repo.mkdir()
    run_git(data_repo, "init")
    configure_git_identity(data_repo)
    settings = data_repo / "claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")
    run_git(data_repo, "add", ".")
    run_git(data_repo, "commit", "-m", "initial")
    config = tmp_path / "config.json"

    env = os.environ.copy()
    env["AI_CONFIG_CONFIG"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_config",
            "setup",
            "--data-dir",
            str(data_repo),
            "--repo-url",
            str(tmp_path / "missing.git"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert not config.exists()
    assert run_git(data_repo, "remote") == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_setup_rejects_remote_without_real_push_access(tmp_path: Path) -> None:
    remote, seed = create_data_remote(tmp_path)
    config = tmp_path / "config.json"
    protected_paths = [remote, *remote.rglob("*")]
    original_modes = {path: path.stat().st_mode for path in protected_paths}
    for path in protected_paths:
        path.chmod(path.stat().st_mode & ~0o222)

    env = os.environ.copy()
    env["AI_CONFIG_CONFIG"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ai_config",
                "setup",
                "--data-dir",
                str(seed),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        for path in reversed(protected_paths):
            path.chmod(original_modes[path])

    assert result.returncode != 0
    assert "Push permission verification failed" in result.stderr
    assert not config.exists()


def test_setup_rejects_repository_url_credentials(tmp_path: Path) -> None:
    data_repo = tmp_path / "data"
    config = tmp_path / "config.json"
    env = os.environ.copy()
    env["AI_CONFIG_CONFIG"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_config",
            "setup",
            "--data-dir",
            str(data_repo),
            "--repo-url",
            "https://user:sensitive-value@example.com/private.git",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "sensitive-value" not in result.stderr + result.stdout
    assert not config.exists()


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


def test_frozen_cli_ignores_extraction_directory_as_checkout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    default_data_repo = home / "ai-config" / "data"
    (default_data_repo / "claude").mkdir(parents=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("AI_CONFIG_REPO", None)
    env["AI_CONFIG_CONFIG"] = str(tmp_path / "missing-config.json")
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.frozen = True; "
            "from ai_config.paths import SCRIPT_DIR; print(SCRIPT_DIR)",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    assert Path(result.stdout.strip()) == default_data_repo.resolve()


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
    assert "setup" in result.stderr


@pytest.mark.parametrize("command", ["pull", "sync"])
def test_pull_and_sync_subcommands(tmp_path: Path, command: str) -> None:
    non_git_dir = tmp_path / "non-git"
    non_git_dir.mkdir()
    (non_git_dir / "claude").mkdir()

    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(non_git_dir)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", command],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0

    remote_dir, clone_dir = create_data_remote(tmp_path)

    push_workspace = tmp_path / "push-ws"
    subprocess.run(["git", "clone", str(remote_dir), str(push_workspace)], check=True)
    configure_git_identity(push_workspace)
    commit_and_push_settings(push_workspace, '{"theme": "dark"}', "update remote")

    clone_head_before = run_git(clone_dir, "rev-parse", "HEAD")

    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(clone_dir)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", command],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, (
        f"{command} failed: {result.stderr}\nstdout: {result.stdout}"
    )

    clone_head_after = run_git(clone_dir, "rev-parse", "HEAD")

    assert clone_head_before != clone_head_after
    assert "Status:" in result.stdout


def test_push_collects_commits_and_pushes_selected_tool(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"dark"}\n', encoding="utf-8")

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Configuration changes to commit" in result.stdout
    assert "Local configuration committed and pushed" in result.stdout
    assert run_git(remote, "show", "HEAD:claude/settings.json") == (
        '{"theme":"dark"}'
    )
    assert run_git(data_repo, "status", "--porcelain=v1") == ""
    assert run_git(data_repo, "log", "-1", "--pretty=%s") == (
        "chore: sync claude configuration"
    )


@pytest.mark.parametrize("input_text", ["n\n", ""])
def test_push_cancel_leaves_collected_changes_unstaged(
    tmp_path: Path, input_text: str
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text=input_text,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "collected changes remain unstaged" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "status", "--short") == "M claude/settings.json"
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_refuses_dirty_data_repository_before_init(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")
    (data_repo / "notes.txt").write_text("uncommitted\n", encoding="utf-8")

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "uncommitted changes" in result.stderr
    assert (data_repo / "claude/settings.json").read_text(encoding="utf-8") == "{}"


def test_push_refuses_branch_behind_upstream(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote update")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "ahead 0, behind 1" in result.stderr
    assert "pull before pushing" in result.stdout


def test_push_refuses_branch_ahead_of_upstream(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "local.txt").write_text("local commit\n", encoding="utf-8")
    run_git(data_repo, "add", "local.txt")
    run_git(data_repo, "commit", "-m", "local only")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "ahead 1, behind 0" in result.stderr


def test_push_refuses_detached_head(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    run_git(data_repo, "checkout", "--detach")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "detached HEAD" in result.stderr


def test_push_refuses_branch_without_upstream(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    run_git(data_repo, "branch", "--unset-upstream")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "has no upstream" in result.stderr


def test_push_credential_scan_rejects_staged_credential_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_config import __main__ as main_cli

    _, data_repo = create_data_remote(tmp_path)
    credential = data_repo / "claude/auth.json"
    credential.write_text("not-a-real-token\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/auth.json")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)

    assert main_cli._staged_credentials() == ["claude/auth.json"]


def test_push_no_changes_does_not_create_commit(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")

    result = run_data_cli(data_repo, home, "push", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "No local configuration changes to push" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
