"""One copy of a project's skills, in .agents/skills, copied for Claude."""

import json
import os
from pathlib import Path

import pytest

from ai_config import cli_dispatch, project_skills
from ai_config.project_skills import RECORD


def _write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _source(project: Path, name: str = "review", body: str = "v1\n") -> Path:
    root = project / ".agents/skills" / name
    _write(root / "SKILL.md", body)
    return root


def _copy(project: Path, name: str = "review") -> Path:
    return project / ".claude/skills" / name


def _actions(project: Path) -> dict[str, str]:
    return {step.name: step.action for step in project_skills.plan(project)}


def _sync(project: Path, *flags: str) -> int:
    return project_skills.run_skill_sync([str(project), *flags], "usage")


def test_a_skill_is_copied_for_claude_with_its_files(tmp_path: Path) -> None:
    source = _source(tmp_path)
    _write(source / "scripts/run.sh", "echo hi\n")

    assert _sync(tmp_path) == 0

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "v1\n"
    assert (_copy(tmp_path) / "scripts/run.sh").read_text(encoding="utf-8") == "echo hi\n"
    assert "review" in json.loads((tmp_path / RECORD).read_text(encoding="utf-8"))["skills"]
    assert _actions(tmp_path) == {"review": "ready"}


def test_a_copy_can_be_read_by_others(tmp_path: Path) -> None:
    """A temporary directory is private; swapped in as is, teammates could not read it."""
    source = _source(tmp_path)
    source.chmod(0o755)

    assert _sync(tmp_path) == 0

    if os.name != "nt":
        assert _copy(tmp_path).stat().st_mode & 0o777 == 0o755


def test_editing_the_source_updates_the_copy(tmp_path: Path) -> None:
    _source(tmp_path)
    _sync(tmp_path)
    _source(tmp_path, body="v2\n")

    assert _actions(tmp_path) == {"review": "update"}
    assert _sync(tmp_path) == 0

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "v2\n"
    assert not list((tmp_path / ".claude/skills").glob(".acg-*"))


def test_a_handwritten_skill_of_the_same_name_is_left_alone(tmp_path: Path) -> None:
    _source(tmp_path)
    _write(_copy(tmp_path) / "SKILL.md", "mine\n")

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "mine\n"
    assert not (tmp_path / RECORD).exists()


def test_a_copy_edited_since_is_left_alone(tmp_path: Path) -> None:
    _source(tmp_path)
    _sync(tmp_path)
    _write(_copy(tmp_path) / "SKILL.md", "edited\n")
    _source(tmp_path, body="v2\n")

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "edited\n"


def test_an_identical_skill_is_adopted(tmp_path: Path) -> None:
    _source(tmp_path)
    _write(_copy(tmp_path) / "SKILL.md", "v1\n")

    assert _actions(tmp_path) == {"review": "adopt"}
    assert _sync(tmp_path) == 0
    assert _actions(tmp_path) == {"review": "ready"}


def test_one_conflict_does_not_stop_the_others(tmp_path: Path) -> None:
    _source(tmp_path, "aaa")
    _write(_copy(tmp_path, "aaa") / "SKILL.md", "mine\n")
    _source(tmp_path, "bbb")

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path, "bbb") / "SKILL.md").is_file()


def test_removing_the_source_removes_an_unchanged_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    _sync(tmp_path)
    (source / "SKILL.md").unlink()
    source.rmdir()

    assert _sync(tmp_path) == 0

    assert not _copy(tmp_path).exists()
    assert not (tmp_path / RECORD).exists()


def test_removing_the_source_keeps_an_edited_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    _sync(tmp_path)
    (source / "SKILL.md").unlink()
    source.rmdir()
    _write(_copy(tmp_path) / "SKILL.md", "edited\n")

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "edited\n"


def test_a_source_missing_skill_md_does_not_delete_the_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    _write(source / "notes.txt")
    _sync(tmp_path)
    (source / "SKILL.md").unlink()

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path) / "SKILL.md").is_file()


def test_a_file_added_to_the_copy_counts_as_an_edit(tmp_path: Path) -> None:
    """An update swaps the whole directory; it must not take someone's file with it."""
    source = _source(tmp_path)
    _sync(tmp_path)
    _write(_copy(tmp_path) / "auth.json", "secret\n")
    _write(source / "extra.txt")

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path) / "auth.json").is_file()


def test_credential_files_are_not_copied(tmp_path: Path) -> None:
    source = _source(tmp_path)
    _write(source / "auth.json", "secret\n")
    _write(source / "nested/oauth_creds.json", "secret\n")

    assert _sync(tmp_path) == 0

    assert not (_copy(tmp_path) / "auth.json").exists()
    assert not (_copy(tmp_path) / "nested/oauth_creds.json").exists()
    assert _actions(tmp_path) == {"review": "ready"}


def test_a_copy_changed_after_planning_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source(tmp_path)
    _sync(tmp_path)
    _source(tmp_path, body="v2\n")
    planned = project_skills.plan(tmp_path)

    def racing(project: Path) -> list:
        _write(_copy(tmp_path) / "SKILL.md", "raced\n")
        return planned

    monkeypatch.setattr(project_skills, "plan", racing)

    with pytest.raises(RuntimeError, match="changed during sync"):
        project_skills.sync(tmp_path)

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "raced\n"


def test_line_endings_alone_are_not_an_edit(tmp_path: Path) -> None:
    """The same commit checks out with CRLF under core.autocrlf on Windows."""
    _source(tmp_path)
    _sync(tmp_path)
    (_copy(tmp_path) / "SKILL.md").write_bytes(b"v1\r\n")
    _source(tmp_path, body="v2\n")

    assert _actions(tmp_path) == {"review": "update"}


def test_check_writes_nothing_and_reports_any_difference(tmp_path: Path) -> None:
    _source(tmp_path)

    assert _sync(tmp_path, "--check") == 1
    assert not _copy(tmp_path).exists()
    assert not (tmp_path / RECORD).exists()

    _sync(tmp_path)

    assert _sync(tmp_path, "--check") == 0


@pytest.mark.parametrize("link", [".agents", ".agents/skills", ".claude", ".claude/skills"])
def test_linked_project_directories_are_refused(tmp_path: Path, link: str) -> None:
    project = tmp_path / "proj"
    outside = tmp_path / "outside"
    outside.mkdir()
    _source(project)
    path = project / link
    if path.is_dir():
        for child in sorted(path.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        path.rmdir()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert _sync(project) == 1

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("where", ["source", "copy", "skill"])
def test_links_inside_skills_are_refused(tmp_path: Path, where: str) -> None:
    secret = _write(tmp_path / "secret.txt", "secret\n")
    project = tmp_path / "proj"
    source = _source(project)
    if where == "copy":
        _sync(project)
        _source(project, body="v2\n")
    base = {"source": source / "link.md", "copy": _copy(project) / "link.md",
            "skill": project / ".agents/skills/evil"}[where]
    try:
        base.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert _sync(project) == 1

    assert not any(
        path.is_file() and path.read_text(encoding="utf-8", errors="replace") == "secret\n"
        for path in (project / ".claude").rglob("*") if not path.is_symlink()
    )


@pytest.mark.parametrize("name", ["../../escape", "..", "/etc", "a/b", ".hidden", "a\\b"])
def test_an_unsafe_name_in_the_record_is_refused(tmp_path: Path, name: str) -> None:
    victim = _write(tmp_path / "escape/SKILL.md")
    project = tmp_path / "proj"
    _write(project / RECORD, json.dumps({"skills": {name: "abc"}}))

    assert _sync(project) == 1

    assert victim.is_file()


@pytest.mark.parametrize("text", ["not json", "[]", "{}", '{"skills": []}', '{"skills": {"a": 1}}'])
def test_a_malformed_record_is_refused(tmp_path: Path, text: str) -> None:
    _source(tmp_path)
    _write(tmp_path / RECORD, text)

    assert _sync(tmp_path) == 1

    assert not _copy(tmp_path).exists()


def test_an_empty_project_is_fine(tmp_path: Path) -> None:
    assert _sync(tmp_path) == 0
    assert _sync(tmp_path, "--check") == 0


@pytest.mark.parametrize("args", [["a", "b"], ["--nope"], ["--check", "--check"]])
def test_bad_arguments_print_usage(tmp_path: Path, args: list[str]) -> None:
    assert project_skills.run_skill_sync(args, "usage") == 1


def test_it_works_before_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source is the project, so a missing data repository must not stop it."""
    monkeypatch.setattr(cli_dispatch, "CONFIG_ERROR", "not configured")
    _source(tmp_path)

    assert cli_dispatch.main(["skill", "sync", str(tmp_path)], lambda: None) == 0

    assert (_copy(tmp_path) / "SKILL.md").is_file()


def test_the_home_directory_is_not_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~/.agents/skills is Codex's global folder; syncing it would rewrite global skills."""
    monkeypatch.setattr(project_skills.paths, "HOME", tmp_path)
    _source(tmp_path)

    assert _sync(tmp_path) == 1

    assert not _copy(tmp_path).exists()
    assert not (tmp_path / RECORD).exists()


def test_the_data_repository_is_not_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(project_skills.paths, "SCRIPT_DIR", tmp_path)
    _source(tmp_path)

    assert _sync(tmp_path) == 1

    assert not _copy(tmp_path).exists()


def test_a_missing_source_root_does_not_delete_every_copy(tmp_path: Path) -> None:
    """A sparse checkout or an uninitialised submodule leaves no .agents at all."""
    source = _source(tmp_path)
    _sync(tmp_path)
    (source / "SKILL.md").unlink()
    source.rmdir()
    source.parent.rmdir()
    source.parent.parent.rmdir()

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path) / "SKILL.md").is_file()


def test_a_source_replaced_by_a_file_does_not_delete_the_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    _sync(tmp_path)
    (source / "SKILL.md").unlink()
    source.rmdir()
    _write(source, "not a directory\n")

    assert _sync(tmp_path) == 1

    assert (_copy(tmp_path) / "SKILL.md").is_file()


def test_a_stale_record_is_refreshed_when_source_and_copy_agree(tmp_path: Path) -> None:
    """After a merge or a hand edit of both, the next source edit must not be a conflict."""
    _source(tmp_path)
    _sync(tmp_path)
    _source(tmp_path, body="merged\n")
    _write(_copy(tmp_path) / "SKILL.md", "merged\n")

    assert _sync(tmp_path, "--check") == 1
    assert _sync(tmp_path) == 0
    _source(tmp_path, body="next\n")

    assert _sync(tmp_path) == 0

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "next\n"


def test_a_skill_placed_by_deploy_is_not_a_project_source(tmp_path: Path) -> None:
    """Deploy's copies are someone's personal skills; they must not enter the record."""
    source = _source(tmp_path, "personal")
    _write(tmp_path / ".acg-deploy.json", json.dumps({"files": {
        ".agents/skills/personal/SKILL.md": "abc",
    }}))

    assert _sync(tmp_path) == 0

    assert (source / "SKILL.md").is_file()
    assert not _copy(tmp_path, "personal").exists()
    assert not (tmp_path / RECORD).exists()


def test_a_handwritten_skill_appearing_after_planning_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source(tmp_path)
    planned = project_skills.plan(tmp_path)

    def racing(project: Path) -> list:
        _write(_copy(tmp_path) / "SKILL.md", "mine\n")
        return planned

    monkeypatch.setattr(project_skills, "plan", racing)

    with pytest.raises(RuntimeError, match="changed during sync"):
        project_skills.sync(tmp_path)

    assert (_copy(tmp_path) / "SKILL.md").read_text(encoding="utf-8") == "mine\n"


def test_a_credential_directory_is_not_copied(tmp_path: Path) -> None:
    source = _source(tmp_path)
    _write(source / ".credentials.json/token.txt", "secret\n")

    assert _sync(tmp_path) == 0

    assert not (_copy(tmp_path) / ".credentials.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="NTFS has no executable bit")
def test_an_executable_bit_change_reaches_the_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    script = _write(source / "run.sh", "echo hi\n")
    _sync(tmp_path)
    script.chmod(0o755)

    assert _sync(tmp_path, "--check") == 1
    assert _sync(tmp_path) == 0

    assert (_copy(tmp_path) / "run.sh").stat().st_mode & 0o111


@pytest.mark.skipif(os.name == "nt", reason="NTFS has no such mode")
def test_the_record_can_be_read_by_others(tmp_path: Path) -> None:
    _source(tmp_path)

    assert _sync(tmp_path) == 0

    assert (tmp_path / RECORD).stat().st_mode & 0o044 == 0o044


@pytest.mark.parametrize("name", ["CON", "nul.txt", "foo.", "foo "])
def test_names_windows_cannot_hold_are_refused(tmp_path: Path, name: str) -> None:
    _write(tmp_path / RECORD, json.dumps({"skills": {name: "abc"}}))

    assert _sync(tmp_path) == 1
