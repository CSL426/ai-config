import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from data_repo_helpers import (
    REPO_ROOT,
    commit_and_push_settings,
    configure_git_identity,
    create_data_remote,
    run_data_alias_cli,
    run_data_cli,
    run_git,
)

from ai_config.cli import console_main
from ai_config.commands import setup as setup_cli
from ai_config.commands import setup_git
from ai_config.config import save_data_repo


def project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


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


def test_windows_installer_retries_binary_replacement() -> None:
    installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "function Install-Binary" in installer
    assert "Start-Sleep -Milliseconds 200" in installer
    assert installer.count("Install-Binary ") == 2


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


def test_skill_guide_does_not_require_data_repository(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(tmp_path / "missing-data-repo")
    env["AI_CONFIG_ENTRYPOINT"] = "ai-config"
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "skill"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.startswith("---\nname: acg\n")
    assert "ai-config status" in result.stdout
    assert "permissions" in result.stdout
    assert "Never commit or push without explicit user approval" in result.stdout


def test_skill_guide_rejects_extra_arguments(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(tmp_path / "missing-data-repo")
    env["AI_CONFIG_ENTRYPOINT"] = "ai-config"
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "skill", "extra"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1


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
        setup_git,
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
def test_setup_configures_read_only_remote_with_a_warning(tmp_path: Path) -> None:
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

    # A remote that can be read but not written is a supported setup: the
    # machine keeps status, pull, and apply, and only push is unavailable.
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Push permission verification failed" in output
    assert "read-only" in output
    assert config.exists()


def _run_setup(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AI_CONFIG_CONFIG"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "ai_config", "setup", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_setup_sets_upstream_for_existing_repo_without_remote(
    tmp_path: Path,
) -> None:
    # 情境:Google Drive 模式留下的本機 repo(有 commit、沒 remote),改用 Git
    # 連到同一份設定;setup 必須把 main 綁到 origin/main,否則 pull 立刻失敗。
    remote, seed = create_data_remote(tmp_path)
    branch = run_git(seed, "branch", "--show-current")
    local = tmp_path / "local"
    subprocess.run(["git", "init", "-b", branch, str(local)], check=True)
    configure_git_identity(local)
    seed_head = run_git(seed, "rev-parse", "HEAD")
    run_git(local, "fetch", str(remote), branch)
    run_git(local, "reset", "--hard", "FETCH_HEAD")
    assert run_git(local, "rev-parse", "HEAD") == seed_head

    result = _run_setup(
        tmp_path / "config.json",
        "--data-dir",
        str(local),
        "--repo-url",
        str(remote),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"now tracks origin/{branch}" in output
    assert (
        run_git(local, "rev-parse", "--abbrev-ref", "@{upstream}") == f"origin/{branch}"
    )


def test_setup_adopts_remote_branch_for_unborn_repo(tmp_path: Path) -> None:
    remote, seed = create_data_remote(tmp_path)
    branch = run_git(seed, "branch", "--show-current")
    local = tmp_path / "local"
    subprocess.run(["git", "init", "-b", branch, str(local)], check=True)
    configure_git_identity(local)
    for tool in ("claude", "codex", "agy"):
        (local / tool).mkdir()

    result = _run_setup(
        tmp_path / "config.json",
        "--data-dir",
        str(local),
        "--repo-url",
        str(remote),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "set it as upstream" in output
    assert run_git(local, "rev-parse", "HEAD") == run_git(seed, "rev-parse", "HEAD")
    assert (
        run_git(local, "rev-parse", "--abbrev-ref", "@{upstream}") == f"origin/{branch}"
    )


def test_setup_leaves_unborn_repo_with_files_alone(tmp_path: Path) -> None:
    remote, seed = create_data_remote(tmp_path)
    branch = run_git(seed, "branch", "--show-current")
    local = tmp_path / "local"
    subprocess.run(["git", "init", "-b", branch, str(local)], check=True)
    configure_git_identity(local)
    (local / "claude").mkdir()
    (local / "claude" / "notes.md").write_text("mine", encoding="utf-8")

    result = _run_setup(
        tmp_path / "config.json",
        "--data-dir",
        str(local),
        "--repo-url",
        str(remote),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "upstream not set" in output
    assert (local / "claude" / "notes.md").read_text(encoding="utf-8") == "mine"


def test_setup_rejects_unreachable_remote(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", str(seed)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(seed), "remote", "add", "origin", str(tmp_path / "absent")],
        capture_output=True,
        check=True,
    )
    (seed / "claude").mkdir()
    config = tmp_path / "config.json"
    env = os.environ.copy()
    env["AI_CONFIG_CONFIG"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "setup", "--data-dir", str(seed)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    # Unreadable is fatal: without a fetch nothing can sync at all.
    assert result.returncode != 0
    assert "Read access verification failed" in result.stdout + result.stderr
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
            (
                "import sys; sys.frozen = True; "
                "from ai_config.paths import SCRIPT_DIR; print(SCRIPT_DIR)"
            ),
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
    # 由 git 判斷重疊後拒絕;acg 只負責把原因和下一步講清楚
    assert "本機修改保留" in result.stderr
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before
    assert run_git(data_repo, "status", "--short") == "M claude/settings.json"
    assert run_git(data_repo, "stash", "list") == ""
    assert not (data_repo / ".git/rebase-merge").exists()
    assert not (data_repo / ".git/rebase-apply").exists()
    assert local_settings.read_text(encoding="utf-8") == '{"theme":"local"}\n'


def test_pull_proceeds_with_untracked_files(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    (data_repo / "notes.txt").write_text("local notes\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    # fast-forward 碰不到未追蹤的檔案,拿它們擋 pull 等於還沒 push 的
    # 新技能會讓人完全無法更新
    assert result.returncode == 0, result.stderr + result.stdout
    assert run_git(data_repo, "status", "--short") == "?? notes.txt"
    assert (data_repo / "notes.txt").read_text(encoding="utf-8") == "local notes\n"


def _push_from_another_clone(
    tmp_path: Path, remote: Path, relative: str, content: str
) -> None:
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(remote), str(other)], check=True, capture_output=True
    )
    configure_git_identity(other)
    target = other / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(other, "add", ".")
    run_git(other, "commit", "-m", f"remote edits {relative}")
    run_git(other, "push", "origin", "HEAD")


def test_pull_fast_forwards_around_unrelated_local_changes(tmp_path: Path) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    _push_from_another_clone(tmp_path, remote, "claude/CLAUDE.md", "remote rules\n")
    local = data_repo / "claude" / "settings.json"
    local.write_text('{"changed": true}\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    # git 本來就允許:遠端沒動到本機修改的檔案,fast-forward 安全
    assert result.returncode == 0, result.stdout + result.stderr
    assert "尚未保存的修改" in result.stdout
    assert (data_repo / "claude" / "CLAUDE.md").read_text(
        encoding="utf-8"
    ) == "remote rules\n"
    assert local.read_text(encoding="utf-8") == '{"changed": true}\n'


def test_pull_refuses_when_remote_touches_a_locally_modified_file(
    tmp_path: Path,
) -> None:
    remote, data_repo = create_data_remote(tmp_path)
    _push_from_another_clone(
        tmp_path, remote, "claude/settings.json", '{"remote": 1}\n'
    )
    local = data_repo / "claude" / "settings.json"
    local.write_text('{"changed": true}\n', encoding="utf-8")
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode != 0
    assert "本機修改保留" in result.stderr
    assert "claude/settings.json" in result.stdout
    assert "commit 或 stash" in result.stdout
    # git 拒絕時什麼都沒動
    assert local.read_text(encoding="utf-8") == '{"changed": true}\n'
    assert run_git(data_repo, "rev-parse", "HEAD") == head_before


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


def test_reset_is_not_forceable(tmp_path: Path) -> None:
    # reset 會刪光設定檔,--force 不通過這一關
    _remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "reset", "--force", input_text="")

    assert "Cancelled" in result.stdout


def test_refused_clone_explains_private_repository_and_accounts(monkeypatch) -> None:
    import subprocess

    from ai_config.commands import setup_git

    refused = subprocess.CompletedProcess(
        ["git"],
        128,
        stdout="",
        stderr="remote: Repository not found.\nfatal: repository 'x' not found\n",
    )
    monkeypatch.setattr(setup_git.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        "ai_config.ghauth._logged_in_accounts", lambda: ("first", ["first", "second"])
    )
    message = setup_git._explain_refused_clone(refused, "https://github.com/o/r.git")
    assert "私有" in message and "--account" in message
    assert "first, second" in message

    monkeypatch.setattr(setup_git.shutil, "which", lambda name: None)
    message = setup_git._explain_refused_clone(refused, "https://github.com/o/r.git")
    assert "winget install GitHub.cli" in message

    plain = subprocess.CompletedProcess(
        ["git"], 1, stdout="", stderr="fatal: Could not resolve host\n"
    )
    assert "私有" not in setup_git._explain_refused_clone(
        plain, "https://github.com/o/r.git"
    )


def test_clone_options_bind_the_account_from_the_first_fetch() -> None:
    from ai_config.commands import setup_git

    assert setup_git._clone_options(None) == []
    options = setup_git._clone_options("CSL426")
    # 先清掉全域 helper,再只加 acg 自己的
    assert options[:2] == ["-c", "credential.helper="]
    assert options[3].startswith("credential.helper=!") and options[3].endswith(
        "__git-credential CSL426"
    )


def test_the_plugin_version_matches_the_project() -> None:
    """外掛的版號是手寫的,發版時很容易忘記跟上。

    忘了的話使用者在 `claude plugin list` 看到的是舊版號,會以為自己沒更新。
    """
    import json
    import tomllib

    project = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    plugin = json.loads(
        (REPO_ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]

    assert plugin == project


def test_plugin_commands_never_run_a_placeholder() -> None:
    """`!` 開頭的行會被 Claude Code 直接執行,佔位符會原封不動送進去。

    實際發生過:`!`acg memory handoff write "<名稱>" "<內容>"`` 寫出了一則
    名字就叫「名稱」的交接。要嘛用 $ARGUMENTS,要嘛讓模型自己組指令。
    """
    offenders = []
    for path in sorted((REPO_ROOT / "plugin/commands").glob("*.md")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("!") and ("<" in line and ">" in line):
                offenders.append(f"{path.name}:{number}")

    assert offenders == []


def test_every_plugin_command_declares_what_it_is() -> None:
    """沒有 description 的指令在選單裡只剩名字,使用者看不出它做什麼。"""
    missing = [
        path.name
        for path in sorted((REPO_ROOT / "plugin/commands").glob("*.md"))
        if not path.read_text(encoding="utf-8").startswith("---\n")
        or "description:" not in path.read_text(encoding="utf-8").split("---")[1]
    ]

    assert missing == []


def test_handoff_reminder_management_is_documented_on_agent_surfaces() -> None:
    from ai_config.guide import render_guide

    surfaces = {
        "guide": render_guide(),
        "README": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "plugin": (REPO_ROOT / "plugin/commands/handoff.md").read_text(
            encoding="utf-8"
        ),
    }
    for name, content in surfaces.items():
        for action in ("status", "enable", "disable"):
            assert f"memory handoff remind {action}" in content, (name, action)
        assert "PreCompact" in content, name

    plugin = surfaces["plugin"]
    frontmatter = plugin.split("---", 2)[1]
    assert "Bash(acg memory handoff:*)" in frontmatter
    assert "Bash(ai-config memory handoff:*)" in frontmatter


def test_pull_clears_phantom_line_ending_change(tmp_path: Path) -> None:
    """Windows autocrlf marks a rewritten file modified with an empty diff."""
    remote, data_repo = create_data_remote(tmp_path)
    local = data_repo / "claude" / "settings.json"
    # 兩端都用位元組控制:index 一定是 LF、工作區之後一定是 CRLF。
    # 走 write_text 的話 Windows 落地就是 CRLF,再寫 CRLF 等於沒改,幽靈出不來
    local.write_bytes(b'{\n  "local": true\n}\n')
    run_git(data_repo, "-c", "core.autocrlf=false", "add", "claude/settings.json")
    run_git(data_repo, "-c", "core.autocrlf=false", "commit", "-q", "-m", "lf seed")
    run_git(data_repo, "push", "-q", "origin", "HEAD")
    _push_from_another_clone(
        tmp_path, remote, "claude/settings.json", '{\n  "remote": 1\n}\n'
    )
    run_git(data_repo, "config", "core.autocrlf", "true")
    local.write_bytes(b'{\r\n  "local": true\r\n}\r\n')
    # run_git 會 strip,porcelain 的前導空白會不見
    status = run_git(data_repo, "status", "--porcelain").split()
    if not status:
        pytest.skip("這個 git 沒有產生幽靈標記,清除路徑在此平台未驗證")
    assert status == ["M", "claude/settings.json"]
    assert run_git(data_repo, "diff", "--numstat") == ""
    head_before = run_git(data_repo, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "pull", "claude")

    assert result.returncode == 0, result.stderr
    assert "只有換行差異" in result.stdout
    assert "claude/settings.json" in result.stdout
    assert run_git(data_repo, "rev-parse", "HEAD") != head_before
    assert '"remote": 1' in local.read_text(encoding="utf-8")


def test_plugin_content_changes_carry_a_version_bump() -> None:
    """An edit under plugin/ that keeps the version cannot reach anyone.

    `claude plugin update` compares versions, not content: same number,
    same install, no matter what changed inside. The command files sat
    eleven releases behind precisely because nothing checked this.
    """
    import json
    import subprocess

    tag = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout.strip()
    if not tag:
        pytest.skip("no tag to compare against")

    # 比到工作區,不是只比到 HEAD:忘記升版號通常在還沒提交時就看得出來
    changed = subprocess.run(
        ["git", "diff", "--name-only", tag, "--", "plugin/"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout.strip()
    if not changed:
        return

    released = subprocess.run(
        ["git", "show", f"{tag}:plugin/.claude-plugin/plugin.json"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout
    if not released:
        return
    current = json.loads(
        (REPO_ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]
    assert current != json.loads(released)["version"], (
        f"plugin/ 改了但版號還是 {current};{tag} 之後改的檔案:\n{changed}"
    )


def test_attribution_stays_disabled_in_the_database() -> None:
    """The rule file once claimed this was set when the key did not exist.

    Only the setting stops the trailers; a sentence in a rules file does
    not, which is how one reached a commit. A model agreeing to leave the
    session link out holds until that conversation ends — the next one is
    told to add it again, so nothing short of the setting settles it.

    includeCoAuthoredBy is deprecated and never covered the session link
    at all; attribution replaces it and covers all three.

    sessionUrl: true is the switch turned ON. This test once asserted it,
    guarding the wrong value: every new session was told to end commits
    with a Claude-Session line, and one reached a pushed commit.
    """
    import json

    database = REPO_ROOT / "data/claude/settings.json"
    if not database.is_file():
        pytest.skip("no data repository checked out here")
    settings = json.loads(database.read_text(encoding="utf-8-sig"))
    attribution = settings.get("attribution")

    assert isinstance(attribution, dict), "attribution 不見了,三台都會重新開始加"
    assert attribution.get("sessionUrl") is False, "sessionUrl: true 會叫每個 session 加 Claude-Session"
    assert attribution.get("commit") == ""
    assert attribution.get("pr") == ""
    assert "includeCoAuthoredBy" not in settings, "已棄用,由 attribution 取代"


def test_both_installers_lay_out_versions_the_same_way() -> None:
    """The layout has to exist on every platform, or update means overwrite.

    install.sh grew version directories; install.ps1 did not, and nothing
    compared them. Two Windows updates ran without ever creating one, and
    the running exe was overwritten in place each time — which is exactly
    what the layout exists to avoid, on the one platform that cannot do it.
    """
    posix = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    windows = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")

    for token in ("versions", "active"):
        assert token in posix, f"install.sh lost its {token} handling"
        assert token in windows, f"install.ps1 never learned about {token}"


def test_adoption_retries_and_says_when_it_gives_up() -> None:
    """The old exe is asked for its version the instant it stops running.

    A Windows machine updated from 1.0.67 into the new layout and the
    1.0.67 binary was never adopted: the installer asked it for a version
    while the file was still locked, got nothing, and returned silently.
    The one machine the adoption path exists for is the one where it is
    most likely to be asked too early.
    """
    installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    adopt = installer.split("function Adopt-ExistingBinary")[1].split("\nfunction ")[0]

    assert "Wait-ExecutableReady" in adopt, (
        "adoption reads the version without waiting for the file to be usable"
    )
    assert "Write-Warn" in adopt, "giving up on adoption must not be silent"


def test_the_database_rules_match_the_source_block() -> None:
    """A gather run before a source edit writes the old wording.

    That happened: the rules block gained six handoff headings, the
    gather had already run, and the commit message described the new
    format while the file carried the old one. Every machine then pulled
    a CLAUDE.md that disagreed with the code that generates it, and
    nothing said so.
    """
    from ai_config import memory_paths

    database = REPO_ROOT / "data/claude/CLAUDE.md"
    if not database.is_file():
        pytest.skip("no data repository checked out here")
    stored = database.read_text(encoding="utf-8-sig")
    if memory_paths.BLOCK_BEGIN not in stored:
        pytest.skip("shared memory not enabled in this database")

    begin = stored.index(memory_paths.BLOCK_BEGIN)
    end = stored.index(memory_paths.BLOCK_END) + len(memory_paths.BLOCK_END)

    assert stored[begin:end] == memory_paths.RULES_BLOCK.strip(), (
        "資料庫的規則區塊跟原始碼不一致;改完 RULES_BLOCK 要重跑 acg init claude"
    )


def test_plugin_skills_are_named_and_do_not_shadow_a_command() -> None:
    """A skill fires on what the user says; it needs a description to fire on.

    People say "交接" or "接著做" more often than they type the slash
    command. A skill named like a command would collide with it under the
    plugin's prefix, so one of the two would silently win.
    """
    commands = {path.stem for path in (REPO_ROOT / "plugin/commands").glob("*.md")}
    skills = sorted((REPO_ROOT / "plugin/skills").glob("*/SKILL.md"))
    assert skills, "plugin 沒有任何 skill"
    for path in skills:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), path
        front = text.split("---", 2)[1]
        name = next(
            (line.split(":", 1)[1].strip() for line in front.splitlines()
             if line.startswith("name:")), "",
        )
        assert name == path.parent.name, path
        assert "description:" in front, path
        assert name not in commands, f"{name} 跟指令撞名"


def _no_git_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(key, raising=False)


def test_setup_gives_a_machine_without_identity_one_for_the_data_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 排程的記憶推送沒人在場;沒有身分,commit 每晚失敗而且沒人看得到
    _no_git_identity(monkeypatch, tmp_path)
    repo = tmp_path / "data"
    run_git(tmp_path, "init", "-q", str(repo))

    setup_git._ensure_commit_identity(repo, "someone")

    assert run_git(repo, "config", "--local", "user.name").strip() == "someone"
    assert run_git(repo, "config", "--local", "user.email").strip() == (
        "someone@users.noreply.github.com"
    )
    run_git(repo, "commit", "-q", "--allow-empty", "-m", "scheduled")


def test_setup_leaves_an_existing_identity_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_git_identity(monkeypatch, tmp_path)
    (tmp_path / "empty-gitconfig").write_text(
        "[user]\n\tname = Real Person\n\temail = real@example.com\n", encoding="utf-8",
    )
    repo = tmp_path / "data"
    run_git(tmp_path, "init", "-q", str(repo))

    setup_git._ensure_commit_identity(repo, "someone")

    local = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "user.name"],
        capture_output=True, text=True, check=False,
    )
    assert local.returncode == 1, "已有身分就不該在儲存庫裡蓋一層"


@pytest.mark.parametrize(
    ("command", "usage"),
    [
        (["memory", "--help"], "memory <status"),
        (["memory", "handoff", "--help"], "memory handoff [list"),
        (["memory", "handoff", "remind", "-h"], "memory handoff remind [status"),
        (["memory", "autopush", "--help"], "memory autopush [status"),
        (["msg", "--help"], "msg list | setup"),
        (["update", "--help"], "update [version]"),
    ],
)
def test_subcommand_help_prints_its_usage_and_succeeds(
    tmp_path: Path, command: list[str], usage: str,
) -> None:
    # update --help 以前會被當成版本號,真的去下載一個叫 --help 的版本
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(tmp_path / "missing-data-repo")
    env["AI_CONFIG_ENTRYPOINT"] = "acg"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", *command],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False,
        timeout=60,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"Usage: acg {usage}" in output
    assert "✗" not in output
