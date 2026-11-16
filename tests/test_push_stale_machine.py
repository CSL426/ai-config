"""A push from a machine that never applied the latest configuration keeps the newer files."""

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


def test_a_stale_file_keeps_the_repository_version_without_asking(machine) -> None:
    # 2026-11-13:一台沒套用 wiki888 修正的機器 push,問句只列 commit,按 y 就把修正蓋回舊版
    remote, other, data, home = machine
    applied = run_data_cli(data, home, "apply", "claude")
    assert applied.returncode == 0, applied.stderr + applied.stdout
    commit_and_push_settings(other, json.dumps({"theme": "dark"}), "feat: dark theme")
    assert run_data_cli(data, home, "pull", "claude").returncode == 0
    remote_head = run_git(remote, "rev-parse", "HEAD")

    pushed = run_data_cli(data, home, "push", "claude", "--yes")

    assert pushed.returncode == 0, pushed.stderr + pushed.stdout
    assert "保留資料庫的版本" in pushed.stdout
    assert json.loads((data / "claude/settings.json").read_text())["theme"] == "dark"
    assert run_git(remote, "rev-parse", "HEAD") == remote_head


def test_this_machines_other_edits_still_go_out(machine) -> None:
    remote, _, data, home = _behind(machine)
    skill = home / ".claude/skills/mine/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: mine\ndescription: x\n---\nbody\n", encoding="utf-8")

    pushed = run_data_cli(data, home, "push", "claude", "--yes")

    assert pushed.returncode == 0, pushed.stderr + pushed.stdout
    assert json.loads(run_git(remote, "show", "HEAD:claude/settings.json"))["theme"] == "dark"
    assert "name: mine" in run_git(remote, "show", "HEAD:claude/skills/mine/SKILL.md")


def test_a_file_both_machines_changed_keeps_the_repository_and_saves_this_copy(machine) -> None:
    remote, _, data, home = _behind(machine)
    (home / ".claude/settings.json").write_text('{"theme": "light"}\n', encoding="utf-8")

    pushed = run_data_cli(data, home, "push", "claude", "--yes")

    assert "都改過" in pushed.stdout
    assert json.loads(run_git(remote, "show", "HEAD:claude/settings.json"))["theme"] == "dark"
    saved = list((home / ".ai-config-backup/push-kept-repo").rglob("settings.json"))
    assert saved and json.loads(saved[0].read_text())["theme"] == "light"


def test_a_machine_that_never_applied_is_not_stopped(machine) -> None:
    # 第一台 init 之後直接 push,沒有紀錄可比,不能擋
    _, other, data, home = machine
    commit_and_push_settings(other, json.dumps({"theme": "dark"}), "feat: dark theme")
    assert run_data_cli(data, home, "pull", "claude").returncode == 0

    pushed = run_data_cli(data, home, "push", "claude", "--yes")

    assert "這台上次 apply 之後" not in pushed.stdout


def _behind(machine) -> "tuple[Path, Path, Path, Path]":
    remote, other, data, home = machine
    assert run_data_cli(data, home, "apply", "claude").returncode == 0
    commit_and_push_settings(other, json.dumps({"theme": "dark"}), "feat: dark theme")
    assert run_data_cli(data, home, "pull", "claude").returncode == 0
    return remote, other, data, home



def test_overwrite_newer_is_the_deliberate_way_through(machine) -> None:
    remote, _, data, home = _behind(machine)
    (home / ".claude/settings.json").write_text('{"theme": "light"}\n', encoding="utf-8")

    pushed = run_data_cli(data, home, "push", "claude", "--yes", "--overwrite-newer")

    assert pushed.returncode == 0, pushed.stderr + pushed.stdout
    assert "--overwrite-newer" in pushed.stdout
    assert json.loads(run_git(remote, "show", "HEAD:claude/settings.json"))["theme"] == "light"
