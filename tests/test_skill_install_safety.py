"""Installation failure must not leave a half-installed skill."""

from pathlib import Path

import pytest

from ai_config.commands import skill


@pytest.mark.parametrize("failure", ["copy", "preflight"])
def test_second_destination_failure_rolls_back_new_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repo = tmp_path / "repo"
    live = tmp_path / "home" / ".claude"
    monkeypatch.setattr(skill, "SCRIPT_DIR", repo)
    monkeypatch.setattr(skill, "CLAUDE_HOME", live)
    source = tmp_path / "source"
    source.mkdir()
    original = "---\nname: demo\ndescription: Demo\n---\nBody\n"
    (source / "SKILL.md").write_text(original, encoding="utf-8")
    first = repo / "claude/skills/demo"
    second = live / "skills/demo"
    copytree = skill.shutil.copytree
    plain_path = skill._plain_path

    def fail_copy(src, dst, *args, **kwargs):
        if dst == second:
            raise OSError("Simulated full disk")
        return copytree(src, dst, *args, **kwargs)

    def fail_preflight(path):
        if path == second and first.exists():
            raise ValueError("Simulated changed destination")
        return plain_path(path)

    if failure == "copy":
        monkeypatch.setattr(skill.shutil, "copytree", fail_copy)
    else:
        monkeypatch.setattr(skill, "_plain_path", fail_preflight)

    assert skill.run_skill(["add", str(source)]) == 1
    assert not first.exists()
    assert not second.exists()
    assert (source / "SKILL.md").read_text(encoding="utf-8") == original


def test_delete_rechecks_paths_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    live = tmp_path / "home" / ".claude"
    monkeypatch.setattr(skill, "SCRIPT_DIR", repo)
    monkeypatch.setattr(skill, "CLAUDE_HOME", live)
    roots = [repo / "claude/skills", live / "skills"]
    monkeypatch.setattr(skill, "_skill_roots", lambda: roots)
    first, second = [root / "demo" for root in roots]
    first.mkdir(parents=True)
    (first / "SKILL.md").write_text("Keep\n", encoding="utf-8")

    def confirm_and_change(prompt):
        second.mkdir(parents=True)
        (second / "SKILL.md").write_text("New copy\n", encoding="utf-8")
        return True

    monkeypatch.setattr(skill, "confirm_prompt", confirm_and_change)

    assert skill.run_skill(["remove", "demo"]) == 1
    assert (first / "SKILL.md").read_text(encoding="utf-8") == "Keep\n"
    assert (second / "SKILL.md").read_text(encoding="utf-8") == "New copy\n"
