"""Build identity follows the executable or its own source checkout."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from ai_config import version

COMMIT = "0123456789abcdef" * 2 + "01234567"


def test_frozen_build_uses_embedded_commit_without_git(monkeypatch) -> None:
    monkeypatch.setattr(version.sys, "frozen", True, raising=False)
    monkeypatch.setattr(version, "COMMIT_SHA", COMMIT)

    def unexpected(*args, **kwargs):
        raise AssertionError("A standalone build must not need Git")

    monkeypatch.setattr(version.subprocess, "run", unexpected)
    assert version.current_commit() == COMMIT


def test_source_commit_comes_from_its_checkout(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    # A worktree uses a .git file, not a directory.
    (source / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    monkeypatch.setattr(version, "__file__", str(source / "ai_config/version.py"))
    monkeypatch.setattr(version.sys, "frozen", False, raising=False)
    monkeypatch.setattr(version, "COMMIT_SHA", "stale-packaging-value")

    def git_head(command, **kwargs):
        assert command == [
            "git", "-C", str(source), "rev-parse", "--verify", "HEAD"
        ]
        assert kwargs["timeout"] > 0
        return subprocess.CompletedProcess(command, 0, COMMIT + "\n", "")

    monkeypatch.setattr(version.subprocess, "run", git_head)
    assert version.current_commit() == COMMIT


def test_installed_package_does_not_report_surrounding_repository(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / ".git").mkdir()
    installed = tmp_path / "venv/site-packages/ai_config/version.py"
    monkeypatch.setattr(version, "__file__", str(installed))
    monkeypatch.setattr(version.sys, "frozen", False, raising=False)
    monkeypatch.setattr(version, "COMMIT_SHA", "")

    def unexpected(*args, **kwargs):
        raise AssertionError("An installed package has no source checkout")

    monkeypatch.setattr(version.subprocess, "run", unexpected)
    assert version.current_commit() is None


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError(), subprocess.TimeoutExpired("git", 2)],
)
def test_source_identity_survives_unavailable_git(
    tmp_path, monkeypatch, failure
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        version, "__file__", str(tmp_path / "ai_config/version.py")
    )
    monkeypatch.setattr(version.sys, "frozen", False, raising=False)
    monkeypatch.setattr(version, "COMMIT_SHA", "")

    def unavailable(*args, **kwargs):
        raise failure

    monkeypatch.setattr(version.subprocess, "run", unavailable)
    assert version.current_commit() is None


@pytest.mark.parametrize("commit", [COMMIT, "bad-build-sha"])
def test_release_workflow_embeds_and_validates_commit(
    tmp_path, monkeypatch, commit
) -> None:
    import yaml

    workflow = Path(__file__).resolve().parents[1] / (
        ".github/workflows/standalone-release.yml"
    )
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    step = next(
        step for step in document["jobs"]["build"]["steps"]
        if step.get("name") == "Embed build commit"
    )
    assert step["env"]["BUILD_COMMIT"] == "${{ github.sha }}"
    (tmp_path / "ai_config").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUILD_COMMIT", commit)
    result = subprocess.run(
        [sys.executable, "-c", step["run"]],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if commit != COMMIT:
        assert result.returncode != 0
        assert "Invalid build commit" in result.stderr
        assert not (tmp_path / "ai_config/_build.py").exists()
        return
    assert result.returncode == 0, result.stderr
    metadata = ast.parse(
        (tmp_path / "ai_config/_build.py").read_text(encoding="utf-8")
    )
    assignment = metadata.body[-1]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.targets[0], ast.Name)
    assert assignment.targets[0].id == "COMMIT_SHA"
    assert ast.literal_eval(assignment.value) == COMMIT
