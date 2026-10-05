"""setup and pull: cloning, remotes, upstream, identity, and fast-forward safety."""

import json
import os
import shutil
import subprocess
import sys
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
from packaging_test_helpers import (
    project_version,
)

from ai_config.commands import setup as setup_cli
from ai_config.commands import setup_git
from ai_config.config import save_data_repo


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
        "ai_config.ghauth_login._logged_in_accounts", lambda: ("first", ["first", "second"])
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


def test_setup_ends_by_saying_what_to_run_next(tmp_path: Path) -> None:
    # setup 以前只印出存檔位置,新手不知道接著要 init 還是 apply
    remote, _ = create_data_remote(tmp_path)
    config = tmp_path / "config" / "config.json"
    data_repo = tmp_path / "data"
    data_repo.mkdir()

    result = _run_setup(config, "--data-dir", str(data_repo), "--repo-url", str(remote))

    assert result.returncode == 0, result.stderr + result.stdout
    # 遠端已經有 claude 的設定:這台是第二台,該 apply
    assert "下一步" in result.stdout
    assert " apply " in result.stdout
    assert " init " not in result.stdout
    assert "memory enable" in result.stdout


def test_an_empty_repository_is_told_to_init_and_push(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "data"
    for tool in ("claude", "codex", "agy"):
        (empty / tool).mkdir(parents=True)
    (empty / "claude/.gitkeep").write_text("", encoding="utf-8")

    setup_cli._print_next_steps(empty)

    out = capsys.readouterr().out
    assert " init " in out and " push " in out
    assert " apply " not in out
