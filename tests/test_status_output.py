"""Status output stays readable on machines with many tool-provided skills."""

from pathlib import Path

import pytest

from ai_config.commands import status as status_cmd


def _make_unmanaged(store: Path, names: "list[str]") -> None:
    store.mkdir(parents=True, exist_ok=True)
    for name in names:
        (store / name).mkdir(exist_ok=True)
        (store / name / "SKILL.md").write_text("x", encoding="utf-8")
    (store / ".ai-config-managed").write_text("", encoding="utf-8")


def test_many_unmanaged_skills_collapse_to_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "skills"
    names = [f"skill-{index:02d}" for index in range(26)]
    _make_unmanaged(store, names)
    monkeypatch.setattr(status_cmd, "_skill_stores", lambda tool: [("codex", store)])

    status_cmd.check_unmanaged_skills("codex")
    captured = capsys.readouterr()
    text = captured.out + captured.err

    # 26 個技能不該印成 26 行
    assert len(text.rstrip().splitlines()) < 6
    assert "26 skill(s)" in text
    assert "skill-00, skill-01" in text


def test_few_unmanaged_skills_are_listed_individually(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "skills"
    _make_unmanaged(store, ["alpha", "beta"])
    monkeypatch.setattr(status_cmd, "_skill_stores", lambda tool: [("codex", store)])

    status_cmd.check_unmanaged_skills("codex")
    text = capsys.readouterr().out

    assert "alpha" in text and "beta" in text
    # 少量時維持逐行,方便直接複製單一名稱
    assert "alpha, beta" not in text


def test_the_store_path_is_printed_once_not_per_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "skills"
    _make_unmanaged(store, [f"skill-{index:02d}" for index in range(10)])
    monkeypatch.setattr(status_cmd, "_skill_stores", lambda tool: [("codex", store)])

    status_cmd.check_unmanaged_skills("codex")
    text = capsys.readouterr().out

    assert text.count(str(store)) == 1
