"""A push from a machine that never applied the latest configuration asks first."""

import json
from pathlib import Path

import pytest
from data_repo_helpers import (
    commit_and_push_settings,
    configure_git_identity,
    create_data_remote,
    run_data_cli,
    run_git,
)


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "tuple[Path, Path, Path, Path]":
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_CONFIG_NO_AUTOPUSH", "1")
    remote, other = create_data_remote(tmp_path)
    data = tmp_path / "data"
    run_git(tmp_path, "clone", "-q", str(remote), str(data))
    configure_git_identity(data)
    home = tmp_path / "home"
    home.mkdir()
    return remote, other, data, home


def test_push_asks_before_gathering_over_another_machines_update(machine) -> None:
    # 一台還沒 apply 最新設定就 push all,差點把別台一整天的技能修正蓋回舊版
    remote, other, data, home = machine
    applied = run_data_cli(data, home, "apply", "claude")
    assert applied.returncode == 0, applied.stderr + applied.stdout

    commit_and_push_settings(other, json.dumps({"theme": "dark"}), "feat: dark theme")
    pulled = run_data_cli(data, home, "pull", "claude")
    assert pulled.returncode == 0, pulled.stderr + pulled.stdout
    remote_head = run_git(remote, "rev-parse", "HEAD")

    pushed = run_data_cli(data, home, "push", "claude", "--force")

    # --force 也跳不過:沒有終端機回答,就是不推
    assert pushed.returncode == 1, pushed.stdout
    assert "這台上次 apply 之後" in pushed.stdout
    assert "feat: dark theme" in pushed.stdout
    assert "apply claude" in pushed.stdout
    assert json.loads((data / "claude/settings.json").read_text())["theme"] == "dark"
    assert run_git(remote, "rev-parse", "HEAD") == remote_head

    # 套用之後就跟資料庫一致,push 不再攔
    assert run_data_cli(data, home, "apply", "claude").returncode == 0
    again = run_data_cli(data, home, "push", "claude", "--force")
    assert "這台上次 apply 之後" not in again.stdout
    assert again.returncode == 0, again.stderr + again.stdout


def test_a_machine_that_never_applied_is_not_stopped(machine) -> None:
    # 第一台 init 之後直接 push,沒有紀錄可比,不能擋
    _, other, data, home = machine
    commit_and_push_settings(other, json.dumps({"theme": "dark"}), "feat: dark theme")
    assert run_data_cli(data, home, "pull", "claude").returncode == 0

    pushed = run_data_cli(data, home, "push", "claude", "--force")

    assert "這台上次 apply 之後" not in pushed.stdout
