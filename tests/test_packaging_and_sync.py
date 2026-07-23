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


def project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


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


def test_unix_installer_refreshes_command_cache_with_completion() -> None:
    installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'Activate in this shell: hash -r && source \\"$completion_file\\"' in (
        installer
    )


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


@pytest.mark.parametrize("command", ["version", "--version", "-V"])
def test_version_commands_do_not_require_data_repository(
    tmp_path: Path,
    command: str,
) -> None:
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(tmp_path / "missing-data-repo")
    env["AI_CONFIG_ENTRYPOINT"] = "ai-config"
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", command],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == f"ai-config (acg) {project_version()}"


@pytest.mark.parametrize("command", ["version", "--version", "-V"])
@pytest.mark.parametrize(
    ("executable", "display_name"),
    [
        ("ai-config.exe", "ai-config (acg)"),
        ("acg", "ai-config (acg)"),
        ("acg.exe", "ai-config (acg)"),
    ],
)
def test_console_main_version_uses_shared_product_name(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    executable: str,
    display_name: str,
) -> None:
    environment = os.environ.copy()
    environment.pop("AI_CONFIG_ENTRYPOINT", None)
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(sys, "argv", [executable, command])

    assert console_main() == 0
    assert capsys.readouterr().out.strip() == f"{display_name} {project_version()}"


def test_lowercase_v_is_not_a_version_alias(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "-v")

    assert result.returncode == 1
    assert "Unknown command: -v" in result.stderr


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


@pytest.mark.parametrize("command", ["pull", "sync"])
def test_pull_refuses_dirty_conflicting_change_without_autostash(
    tmp_path: Path,
    command: str,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote change")
    local_settings = data_repo / "claude/settings.json"
    local_settings.write_text('{"theme":"local"}\n', encoding="utf-8")
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, command, "claude")

    assert result.returncode != 0
    assert "uncommitted changes" in result.stderr
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "status", "--short") == "M claude/settings.json"
    assert run_git(data_repo, "stash", "list") == ""
    assert not (data_repo / ".git/rebase-merge").exists()
    assert not (data_repo / ".git/rebase-apply").exists()
    assert local_settings.read_text(encoding="utf-8") == '{"theme":"local"}\n'


def test_pull_refuses_untracked_files(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "notes.txt").write_text("local notes\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode != 0
    assert "uncommitted changes" in result.stderr
    assert run_git(data_repo, "status", "--short") == "?? notes.txt"


def test_pull_refuses_local_ahead_branch(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "local.txt").write_text("local commit\n", encoding="utf-8")
    run_git(data_repo, "add", "local.txt")
    run_git(data_repo, "commit", "-m", "local change")
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode != 0
    assert "ahead 1, behind 0" in result.stderr
    assert "push to publish" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "status", "--short") == ""


def test_pull_refuses_diverged_branch_without_starting_rebase(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    run_git(data_repo, "commit", "-m", "local change")
    head_before = run_git(data_repo, "rev-parse", "HEAD")

    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote change")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode != 0
    assert "ahead 1, behind 1" in result.stderr
    assert "diverged branch manually" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "status", "--short") == ""
    assert not (data_repo / ".git/rebase-merge").exists()
    assert not (data_repo / ".git/rebase-apply").exists()
    assert settings.read_text(encoding="utf-8") == '{"theme":"local"}\n'


def test_pull_refuses_existing_git_operation(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / ".git/rebase-merge").mkdir()
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode != 0
    assert "rebase in progress" in result.stderr


def test_pull_refuses_detached_head(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    run_git(data_repo, "checkout", "--detach")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode != 0
    assert "detached HEAD" in result.stderr


def test_pull_refuses_branch_without_upstream(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    run_git(data_repo, "branch", "--unset-upstream")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode != 0
    assert "has no upstream" in result.stderr


def test_pull_reports_already_synchronized_repository(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "already up to date" in result.stdout


@pytest.mark.parametrize("command", ["pull", "sync"])
def test_acg_alias_runs_pull_commands(tmp_path: Path, command: str) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_alias_cli(data_repo, home, command, "claude")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "already up to date" in result.stdout
    assert "Run acg apply to deploy" in result.stdout


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
        "chore: update claude settings"
    )


def test_push_commits_new_file_after_review(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    claude_home = home / ".claude"
    claude_home.mkdir()
    (claude_home / "settings.json").write_text("{}", encoding="utf-8")
    (claude_home / "CLAUDE.md").write_text(
        "new reviewed instructions\n",
        encoding="utf-8",
    )

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "+new reviewed instructions" in result.stdout
    assert run_git(remote, "show", "HEAD:claude/CLAUDE.md") == (
        "new reviewed instructions"
    )
    assert run_git(data_repo, "status", "--porcelain=v1") == ""


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
    assert "configuration changes remain unstaged" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "status", "--short") == "M claude/settings.json"
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_review_displays_new_file_content(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    claude_home = home / ".claude"
    claude_home.mkdir()
    (claude_home / "settings.json").write_text("{}", encoding="utf-8")
    (claude_home / "CLAUDE.md").write_text(
        "new instructions visible in review\n",
        encoding="utf-8",
    )

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "new file mode" in result.stdout
    assert "+new instructions visible in review" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "status", "--short") == "?? claude/CLAUDE.md"


def test_acg_alias_runs_push_command(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")

    result = run_data_alias_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Commit and push these changes?" in result.stdout
    assert "configuration changes remain unstaged" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""


def test_push_refuses_dirty_path_outside_selected_tool(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")
    (data_repo / "notes.txt").write_text("uncommitted\n", encoding="utf-8")

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "outside the selected tools" in result.stderr
    assert "notes.txt" in result.stdout
    assert (data_repo / "claude/settings.json").read_text(encoding="utf-8") == "{}"


def test_push_reviews_and_publishes_existing_uncommitted_changes(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    repo_settings = data_repo / "claude/settings.json"
    repo_settings.write_text('{"theme":"collected"}\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    live_settings = home / ".claude/settings.json"
    live_settings.parent.mkdir()
    live_settings.write_text('{"theme":"live"}\n', encoding="utf-8")

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Reviewing existing uncommitted configuration changes" in result.stdout
    assert '-{"theme":"collected"}' not in result.stdout
    assert '+{"theme":"collected"}' in result.stdout
    assert "Commit message: chore: update claude settings" in result.stdout
    assert "Init Claude" not in result.stdout
    assert run_git(remote, "show", "HEAD:claude/settings.json") == (
        '{"theme":"collected"}'
    )
    assert run_git(data_repo, "status", "--short") == ""


def test_push_commit_message_uses_tools_and_changed_json_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config import __main__ as main_cli

    _, data_repo = create_data_remote(tmp_path)
    claude_settings = data_repo / "claude/settings.json"
    claude_settings.write_text('{"model":"claude-sonnet"}\n', encoding="utf-8")
    agy_settings = data_repo / "agy/settings.json"
    agy_settings.parent.mkdir()
    agy_settings.write_text('{"model":"gemini-flash"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json", "agy/settings.json")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)

    assert main_cli._proposed_push_commit_message(
        ["agy/settings.json", "claude/settings.json"]
    ) == "chore: update claude and agy model settings"


def test_push_commit_message_identifies_shared_skill() -> None:
    from ai_config import __main__ as main_cli

    assert main_cli._proposed_push_commit_message(
        [
            "claude/shared/both/ci-check/SKILL.md",
            "claude/shared/both/ci-check/scripts/check.py",
        ]
    ) == "chore: update ci-check shared skill"


def test_push_cancel_preserves_existing_changes_unstaged(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"collected"}\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "configuration changes remain unstaged" in result.stdout
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_refuses_pre_staged_changes_without_altering_index(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"staged"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "pre-staged changes" in result.stderr
    assert run_git(data_repo, "diff", "--cached", "--name-only") == (
        "claude/settings.json"
    )
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_refuses_uncommitted_credential_file(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    credential = data_repo / "claude/auth.json"
    credential.write_text("placeholder\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "credential files" in result.stderr
    assert run_git(data_repo, "status", "--short") == "?? claude/auth.json"
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/auth.json" not in remote_tree


def test_push_refuses_dirty_repository_with_ahead_commit(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/CLAUDE.md"
    instructions.write_text("local commit\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/CLAUDE.md")
    run_git(data_repo, "commit", "-m", "local commit")
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"dirty"}\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "both uncommitted changes and unpublished" in result.stderr
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_refuses_dirty_repository_behind_upstream(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"dirty"}\n', encoding="utf-8")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote update")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "ahead 0, behind 1" in result.stderr
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"


def test_push_refuses_existing_git_operation(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / ".git/rebase-merge").mkdir()
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "rebase in progress" in result.stderr


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


def test_push_rejects_ahead_commit_outside_selected_tool(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "local.txt").write_text("local commit\n", encoding="utf-8")
    run_git(data_repo, "add", "local.txt")
    run_git(data_repo, "commit", "-m", "local only")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode != 0
    assert "outside the selected tools" in result.stderr
    assert "local.txt" in result.stdout


def test_push_publishes_reviewed_ahead_commit_without_gathering(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/CLAUDE.md"
    instructions.write_text("reviewed local instructions\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/CLAUDE.md")
    run_git(data_repo, "commit", "-m", "fix: local instructions")
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "claude", input_text="y\n")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "fix: local instructions" in result.stdout
    assert "+reviewed local instructions" in result.stdout
    assert "Push these existing local commits?" in result.stdout
    assert "Existing local commits pushed" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "rev-list", "--count", "@{upstream}..HEAD") == "0"
    assert (
        run_git(remote, "show", "HEAD:claude/CLAUDE.md")
        == "reviewed local instructions"
    )


def test_acg_push_can_cancel_reviewed_ahead_commit(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/CLAUDE.md"
    instructions.write_text("keep this local\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/CLAUDE.md")
    run_git(data_repo, "commit", "-m", "fix: local only")
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_alias_cli(
        data_repo,
        home,
        "push",
        "claude",
        input_text="n\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Cancelled; existing local commits were not pushed" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "rev-list", "--count", "@{upstream}..HEAD") == "1"
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/CLAUDE.md" not in remote_tree


def test_push_rejects_secret_removed_by_later_ahead_commit(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text(
        '{"github_token":"not-a-real-token"}\n',
        encoding="utf-8",
    )
    run_git(data_repo, "add", "claude/settings.json")
    run_git(data_repo, "commit", "-m", "local secret")
    settings.write_text('{"theme":"safe"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    run_git(data_repo, "commit", "-m", "remove local secret")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "all", input_text="y\n")

    assert result.returncode != 0
    assert "Potential credential content exists" in result.stderr
    assert "not-a-real-token" not in result.stdout + result.stderr
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"


def test_push_rejects_credential_file_in_ahead_commit(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    credential = data_repo / "claude/auth.json"
    credential.write_text("placeholder\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/auth.json")
    run_git(data_repo, "commit", "-m", "local credential")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "all", input_text="y\n")

    assert result.returncode != 0
    assert "credential files" in result.stderr
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/auth.json" not in remote_tree


def test_push_rejects_merge_commit_in_ahead_range(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    base_branch = run_git(data_repo, "branch", "--show-current")
    run_git(data_repo, "checkout", "-b", "local-side")
    side_file = data_repo / "claude/side.md"
    side_file.write_text("side\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/side.md")
    run_git(data_repo, "commit", "-m", "local side")
    run_git(data_repo, "checkout", base_branch)
    base_file = data_repo / "claude/base.md"
    base_file.write_text("base\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/base.md")
    run_git(data_repo, "commit", "-m", "local base")
    run_git(data_repo, "merge", "--no-ff", "local-side", "-m", "local merge")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "push", "all", input_text="y\n")

    assert result.returncode != 0
    assert "contains a merge commit" in result.stderr
    remote_tree = run_git(remote, "ls-tree", "-r", "--name-only", "HEAD")
    assert "claude/base.md" not in remote_tree
    assert "claude/side.md" not in remote_tree


def test_ahead_push_rejects_upstream_change_after_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config import __main__ as main_cli

    remote, data_repo = create_data_remote(tmp_path)
    local_file = data_repo / "claude/local.md"
    local_file.write_text("local\n", encoding="utf-8")
    run_git(data_repo, "add", "claude/local.md")
    run_git(data_repo, "commit", "-m", "local update")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)
    snapshot = main_cli._push_snapshot()
    assert snapshot is not None
    commits = main_cli._ahead_commits(snapshot)
    assert commits is not None

    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True)
    configure_git_identity(other)
    commit_and_push_settings(other, '{"theme":"remote"}', "remote update")

    assert not main_cli._ahead_push_matches(
        snapshot,
        ["claude"],
        commits,
    )
    assert "upstream changed after review" in capsys.readouterr().err


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


def test_push_rejects_staged_path_outside_selected_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config import __main__ as main_cli

    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "notes.txt").write_text("concurrent staging\n", encoding="utf-8")
    run_git(data_repo, "add", "notes.txt")
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"local"}\n', encoding="utf-8")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)

    assert main_cli._stage_push_changes(["claude"]) is None
    captured = capsys.readouterr()
    assert "outside the selected tools" in captured.err
    assert "notes.txt" in captured.out
    assert run_git(data_repo, "diff", "--cached", "--name-only") == "notes.txt"
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"


def test_push_rejects_potential_credential_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config import __main__ as main_cli

    _, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"github_token":"not-a-real-token"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)

    assert not main_cli._validate_staged_push(["claude"])
    captured = capsys.readouterr()
    assert "Potential credential content" in captured.err
    assert "claude/settings.json" in captured.out
    assert "not-a-real-token" not in captured.out + captured.err


def test_push_scans_staged_blob_with_unicode_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config import __main__ as main_cli

    _, data_repo = create_data_remote(tmp_path)
    instructions = data_repo / "claude/機密說明.md"
    instructions.write_text(
        "Never store secrets here.\ngithub_token = placeholder\n",
        encoding="utf-8",
    )
    run_git(data_repo, "add", "claude/機密說明.md")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)

    assert main_cli._staged_secret_paths() == ["claude/機密說明.md"]


def test_push_rejects_root_file_named_like_selected_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config import __main__ as main_cli

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    (repo / "claude").write_text("not a tool directory\n", encoding="utf-8")
    run_git(repo, "add", "claude")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", repo)

    assert main_cli._staged_paths_outside(["claude"]) == ["claude"]


def test_push_rejects_restaged_content_changed_after_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config import __main__ as main_cli

    _, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"reviewed"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)
    reviewed_diff = main_cli._staged_diff()
    assert reviewed_diff is not None

    settings.write_text('{"theme":"changed"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")

    assert not main_cli._staged_push_matches(["claude"], reviewed_diff)
    assert "changed after review" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="requires an executable POSIX Git hook")
def test_push_rolls_back_commit_changed_by_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config import __main__ as main_cli

    remote, data_repo = create_data_remote(tmp_path)
    settings = data_repo / "claude/settings.json"
    settings.write_text('{"theme":"reviewed"}\n', encoding="utf-8")
    run_git(data_repo, "add", "claude/settings.json")
    parent = run_git(data_repo, "rev-parse", "HEAD")
    monkeypatch.setattr(main_cli, "SCRIPT_DIR", data_repo)
    reviewed_diff = main_cli._staged_diff()
    assert reviewed_diff is not None

    hook = data_repo / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"theme\":\"changed-by-hook\"}' "
        "> claude/settings.json\n"
        "git add claude/settings.json\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    assert (
        main_cli._commit_and_push(
            "chore: sync claude configuration",
            ["claude"],
            reviewed_diff,
        )
        == 1
    )
    assert run_git(data_repo, "rev-parse", "HEAD") == parent
    assert run_git(data_repo, "diff", "--cached", "--name-only") == ""
    assert run_git(data_repo, "diff", "--name-only") == "claude/settings.json"
    assert run_git(remote, "show", "HEAD:claude/settings.json") == "{}"
    captured = capsys.readouterr()
    assert "differed from the reviewed snapshot" in captured.err
    assert "rolled back and not pushed" in captured.out


def test_push_unstages_selected_tools_when_confirmation_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config import __main__ as main_cli

    status = subprocess.CompletedProcess([], 0, " M claude/settings.json\n", "")
    monkeypatch.setattr(
        main_cli,
        "_push_preflight",
        lambda selected: main_cli._PushPreflight(ahead=0, has_changes=False),
    )
    monkeypatch.setattr(main_cli, "_init_tools", lambda tool: True)
    monkeypatch.setattr(main_cli, "_run_repo_git", lambda *args: status)
    monkeypatch.setattr(main_cli, "_selected_tools", lambda tool: ["claude"])
    monkeypatch.setattr(
        main_cli,
        "_stage_push_changes",
        lambda selected: "reviewed diff\n",
    )

    def interrupt_review(*args: object) -> bool:
        raise KeyboardInterrupt

    restored: list[list[str]] = []
    monkeypatch.setattr(main_cli, "_review_and_confirm_push", interrupt_review)
    monkeypatch.setattr(
        main_cli,
        "_unstage_tools",
        lambda selected: restored.append(selected) is None,
    )

    with pytest.raises(KeyboardInterrupt):
        main_cli.do_push("claude")
    assert restored == [["claude"]]


def test_push_reports_failed_unstage_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config import __main__ as main_cli

    status = subprocess.CompletedProcess([], 0, " M claude/settings.json\n", "")
    monkeypatch.setattr(
        main_cli,
        "_push_preflight",
        lambda selected: main_cli._PushPreflight(ahead=0, has_changes=False),
    )
    monkeypatch.setattr(main_cli, "_init_tools", lambda tool: True)
    monkeypatch.setattr(main_cli, "_run_repo_git", lambda *args: status)
    monkeypatch.setattr(main_cli, "_selected_tools", lambda tool: ["claude"])
    monkeypatch.setattr(
        main_cli,
        "_stage_push_changes",
        lambda selected: "reviewed diff\n",
    )
    monkeypatch.setattr(
        main_cli,
        "_review_and_confirm_push",
        lambda *args: False,
    )
    monkeypatch.setattr(main_cli, "_unstage_tools", lambda selected: False)

    assert main_cli.do_push("claude") == 1
    assert "failed to restore" in capsys.readouterr().err


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
