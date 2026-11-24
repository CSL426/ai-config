"""acg repos: commit, rebase onto the remote, push; anything doubtful stops one repo only."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from ai_config import autopush, locking, memory_hooks, nightly, paths, repos


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", check=True,
    )
    return done.stdout.strip()


def identify(repo: Path) -> None:
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(locking, "BACKUP_BASE", tmp_path / "backup")
    monkeypatch.setattr(autopush, "platform_name", lambda: "linux")
    monkeypatch.setattr(autopush, "_systemctl", Mock(return_value=Mock(returncode=0, stderr="")))
    monkeypatch.setattr(autopush, "forget_missed_runs", lambda timer: None)
    monkeypatch.setattr(autopush, "_claim_slot", lambda hour: None)
    monkeypatch.setattr(paths, "scheduled_command", lambda: ["acg"])
    return tmp_path


@pytest.fixture
def remote(machine: Path) -> Path:
    bare = machine / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = machine / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    identify(seed)
    git(seed, "checkout", "-q", "-b", "main")
    (seed / "notes.md").write_text("one\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "-u", "origin", "main")
    return bare


def clone(remote: Path, name: str) -> Path:
    target = remote.parent / name
    subprocess.run(["git", "clone", "-q", str(remote), str(target)], check=True)
    identify(target)
    return target.resolve()


def remote_log(remote: Path) -> "list[str]":
    return git(remote, "log", "--format=%s", "main").splitlines()


def test_nothing_to_do_is_a_success(remote: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)

    code, done = repos.sync()

    assert code == 0
    assert done[0][1]["ok"] is True
    assert remote_log(remote) == ["seed"]


def test_local_changes_are_committed_and_pushed(remote: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    (repo / "notes.md").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "新筆記.md").write_text("中文\n", encoding="utf-8")

    code, done = repos.sync()

    assert code == 0, done
    assert remote_log(remote)[0].startswith("chore: acg repos sync (")
    assert "新筆記.md" in git(remote, "ls-tree", "--name-only", "main")
    assert git(repo, "status", "--porcelain") == ""


def test_another_machines_work_is_pulled_under_ours(remote: Path) -> None:
    mine, theirs = clone(remote, "a"), clone(remote, "b")
    (theirs / "theirs.md").write_text("x\n", encoding="utf-8")
    git(theirs, "add", "-A")
    git(theirs, "commit", "-q", "-m", "theirs")
    git(theirs, "push", "-q")
    repos.add(mine)
    (mine / "mine.md").write_text("y\n", encoding="utf-8")

    code, _ = repos.sync()

    assert code == 0
    assert (mine / "theirs.md").is_file()
    log = remote_log(remote)
    assert log[1:] == ["theirs", "seed"]
    assert log[0].startswith("chore: acg repos sync")


def test_a_conflict_aborts_and_keeps_the_local_commit(remote: Path) -> None:
    mine, theirs = clone(remote, "a"), clone(remote, "b")
    (theirs / "notes.md").write_text("theirs\n", encoding="utf-8")
    git(theirs, "commit", "-qam", "theirs")
    git(theirs, "push", "-q")
    repos.add(mine)
    (mine / "notes.md").write_text("mine\n", encoding="utf-8")

    code, done = repos.sync()

    assert code == 1
    assert "衝突" in done[0][1]["note"]
    assert not (Path(git(mine, "rev-parse", "--absolute-git-dir")) / "rebase-merge").exists()
    assert git(mine, "status", "--porcelain") == ""
    assert (mine / "notes.md").read_text(encoding="utf-8") == "mine\n"
    assert git(mine, "log", "-1", "--format=%s").startswith("chore: acg repos sync")
    assert remote_log(remote) == ["theirs", "seed"]
    assert repos.failures()[0][0] == mine


def test_a_line_that_looks_like_a_credential_commits_nothing(remote: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    (repo / ".env").write_text("GITHUB_TOKEN=ghp_" + "Ab1" * 12 + "\n", encoding="utf-8")
    (repo / "notes.md").write_text("one\nsafe\n", encoding="utf-8")

    code, done = repos.sync()

    assert code == 1
    assert ".env" in done[0][1]["note"]
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert git(repo, "log", "-1", "--format=%s") == "seed"
    assert remote_log(remote) == ["seed"]


def test_a_repo_mid_rebase_is_left_alone(remote: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    (Path(git(repo, "rev-parse", "--absolute-git-dir")) / "rebase-merge").mkdir()
    (repo / "notes.md").write_text("changed\n", encoding="utf-8")

    code, done = repos.sync()

    assert code == 1
    assert "rebase" in done[0][1]["note"]
    assert git(repo, "log", "-1", "--format=%s") == "seed"


def test_a_detached_head_is_left_alone(remote: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    git(repo, "checkout", "-q", "--detach")

    code, done = repos.sync()

    assert code == 1
    assert "detached" in done[0][1]["note"]


def test_one_stopped_repo_does_not_stop_the_next(remote: Path) -> None:
    broken, fine = clone(remote, "a"), clone(remote, "b")
    repos.add(broken)
    repos.add(fine)
    git(broken, "checkout", "-q", "--detach")
    (fine / "more.md").write_text("z\n", encoding="utf-8")

    code, done = repos.sync()

    assert code == 1
    assert [result["ok"] for _, result in done] == [False, True]
    assert "more.md" in git(remote, "ls-tree", "--name-only", "main")


def test_retrying_one_repo_keeps_the_others_failure(remote: Path) -> None:
    broken, fine = clone(remote, "a"), clone(remote, "b")
    repos.add(broken)
    repos.add(fine)
    git(broken, "checkout", "-q", "--detach")
    repos.sync()

    repos.sync(fine)

    assert [repo for repo, _ in repos.failures()] == [broken]


def test_adding_needs_an_upstream_and_refuses_the_data_repo(
    machine: Path, remote: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lonely = machine / "lonely"
    subprocess.run(["git", "init", "-q", "-b", "main", str(lonely)], check=True)
    with pytest.raises(ValueError, match="追蹤遠端"):
        repos.add(lonely)

    data = clone(remote, "data")
    monkeypatch.setattr(paths, "SCRIPT_DIR", data)
    with pytest.raises(ValueError, match="資料庫"):
        repos.add(data)
    assert repos.registered() == []


def test_the_list_drives_the_nightly_switch(remote: Path) -> None:
    a, b = clone(remote, "a"), clone(remote, "b")

    repos.add(a)
    repos.add(b)
    repos.add(a)

    assert repos.registered() == [a, b]
    assert nightly.enabled("repos") is True
    assert nightly.enabled("autopush") is False

    repos.remove(a)
    assert nightly.enabled("repos") is True
    repos.remove(b)
    assert nightly.enabled("repos") is False
    assert not autopush.schedule_installed()


def test_the_nightly_run_syncs_repos_last(machine: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    autopush.enable(4)
    nightly.turn_on("repos")
    order = []
    monkeypatch.setattr(
        nightly.subprocess, "run",
        lambda argv, **kwargs: order.append(argv) or subprocess.CompletedProcess(argv, 0),
    )

    assert nightly.run(12) == 0

    assert order == [["acg", "memory", "push", "--if-stale", "12"], ["acg", "repos", "sync"]]


def test_a_stopped_repo_is_announced_at_session_start(
    remote: Path, capsys: pytest.CaptureFixture,
) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    git(repo, "checkout", "-q", "--detach")
    repos.sync()

    memory_hooks._announce_failed_repos({"hook_event_name": "SessionStart"})
    said = capsys.readouterr().out
    assert str(repo) in said and "acg repos sync" in said

    memory_hooks._announce_failed_repos({"hook_event_name": "UserPromptSubmit"})
    assert capsys.readouterr().out == ""


def test_the_list_stays_on_this_machine(remote: Path, machine: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)

    assert repos.list_path().is_relative_to(machine / "state")
    assert json.loads(repos.list_path().read_text(encoding="utf-8")) == [str(repo)]


# ─── found by review: each one reproduced before the fix ──────

def test_conflict_markers_from_a_stash_pop_are_never_committed(remote: Path) -> None:
    """stash pop leaves no MERGE_HEAD; the markers were committed and pushed."""
    repo = clone(remote, "a")
    repos.add(repo)
    (repo / "notes.md").write_text("stashed\n", encoding="utf-8")
    git(repo, "stash", "-q")
    (repo / "notes.md").write_text("committed\n", encoding="utf-8")
    git(repo, "commit", "-qam", "local")
    subprocess.run(["git", "-C", str(repo), "stash", "pop", "-q"], capture_output=True, check=False)
    assert git(repo, "ls-files", "--unmerged")

    code, done = repos.sync()

    assert code == 1
    assert "衝突" in done[0][1]["note"]
    assert remote_log(remote) == ["seed"]


def test_a_utf16_file_with_a_token_is_caught(remote: Path) -> None:
    """PowerShell writes UTF-16; git calls it binary and shows no added lines."""
    repo = clone(remote, "a")
    repos.add(repo)
    (repo / "env.ps1").write_bytes(("$env:GH_TOKEN='ghp_" + "Ab1" * 12 + "'\r\n").encode("utf-16"))

    code, done = repos.sync()

    assert code == 1
    assert "env.ps1" in done[0][1]["note"]
    assert remote_log(remote) == ["seed"]


def test_an_added_line_starting_with_two_pluses_is_still_scanned(remote: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    (repo / "notes.md").write_text("one\n++ token = ghp_" + "Ab1" * 12 + "\n", encoding="utf-8")

    code, _ = repos.sync()

    assert code == 1
    assert remote_log(remote) == ["seed"]


def test_a_staged_version_survives_a_stopped_sync(remote: Path) -> None:
    """v1 staged then edited to v2 lived only in the index; reset lost it."""
    repo = clone(remote, "a")
    repos.add(repo)
    (repo / "notes.md").write_text("v1\n", encoding="utf-8")
    git(repo, "add", "notes.md")
    (repo / "notes.md").write_text("v2\n", encoding="utf-8")
    (repo / ".env").write_text("GITHUB_TOKEN=ghp_" + "Ab1" * 12 + "\n", encoding="utf-8")

    code, _ = repos.sync()

    assert code == 1
    assert git(repo, "show", ":notes.md") == "v1"
    assert git(repo, "status", "--porcelain").splitlines() == ["MM notes.md", "?? .env"]


def test_an_untracked_clone_inside_is_not_committed_as_a_gitlink(remote: Path) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    clone(remote, "a/vendor")

    code, done = repos.sync()

    assert code == 1
    assert "vendor" in done[0][1]["note"]
    assert remote_log(remote) == ["seed"]
    assert git(repo, "status", "--porcelain") == "?? vendor/"


def test_a_fork_workflow_is_left_to_a_person(remote: Path, machine: Path) -> None:
    """Pushing to a fork then rebasing onto origin fails every night from the second."""
    fork = machine / "fork.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(remote), str(fork)], check=True)
    repo = clone(remote, "a")
    git(repo, "remote", "add", "fork", str(fork))
    git(repo, "config", "branch.main.pushRemote", "fork")
    repos.add(repo)
    (repo / "more.md").write_text("z\n", encoding="utf-8")

    code, done = repos.sync()

    assert code == 1
    assert "fork" in done[0][1]["note"]
    assert remote_log(remote) == remote_log(fork) == ["seed"]
    assert git(repo, "log", "-1", "--format=%s") == "seed"


def test_a_branch_tracking_another_name_is_left_to_a_person(remote: Path) -> None:
    repo = clone(remote, "a")
    git(repo, "checkout", "-q", "-b", "work", "--track", "origin/main")
    repos.add(repo)
    (repo / "more.md").write_text("z\n", encoding="utf-8")

    code, _ = repos.sync()

    assert code == 1
    assert remote_log(remote) == ["seed"]


def test_a_users_autostash_setting_is_not_used(remote: Path) -> None:
    mine, theirs = clone(remote, "a"), clone(remote, "b")
    (theirs / "t.md").write_text("x\n", encoding="utf-8")
    git(theirs, "add", "-A")
    git(theirs, "commit", "-qm", "theirs")
    git(theirs, "push", "-q")
    git(mine, "config", "rebase.autoStash", "true")
    repos.add(mine)
    (mine / "m.md").write_text("y\n", encoding="utf-8")

    code, _ = repos.sync()

    assert code == 0
    assert git(mine, "stash", "list") == ""


def test_a_repo_that_later_overlaps_the_data_repo_stops(
    remote: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    (repo / "more.md").write_text("z\n", encoding="utf-8")
    monkeypatch.setattr(paths, "SCRIPT_DIR", repo)

    code, done = repos.sync()

    assert code == 1
    assert "資料庫" in done[0][1]["note"]
    assert remote_log(remote) == ["seed"]


def test_a_submodule_the_user_added_is_not_mistaken_for_a_stray_clone(
    remote: Path, machine: Path,
) -> None:
    repo = clone(remote, "a")
    repos.add(repo)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "protocol.file.allow=always",
         "submodule", "add", "-q", str(remote), "lib"],
        check=True, capture_output=True,
    )

    code, done = repos.sync()

    assert code == 0, done
    assert "lib" in git(remote, "ls-tree", "--name-only", "main")
