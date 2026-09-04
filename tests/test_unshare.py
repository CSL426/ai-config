"""Removing a skill from the cross-tool shared area."""

import pytest

from ai_config.commands import share as share_cmd


def _make_shared(root, target: str, name: str) -> None:
    skill = root / "claude" / "shared" / target / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def test_unshare_removes_the_shared_copy(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_cmd, "SCRIPT_DIR", tmp_path)
    _make_shared(tmp_path, "both", "demo")

    assert share_cmd.run_unshare("demo") == 0
    assert not (tmp_path / "claude/shared/both/demo").exists()


def test_unshare_leaves_the_original_skill_alone(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_cmd, "SCRIPT_DIR", tmp_path)
    _make_shared(tmp_path, "both", "demo")
    home_skill = tmp_path / "home" / "skills" / "demo"
    home_skill.mkdir(parents=True)
    (home_skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    monkeypatch.setattr(share_cmd, "CLAUDE_HOME", tmp_path / "home")

    assert share_cmd.run_unshare("demo") == 0
    # 取消分享只收回跨工具副本,不動使用者自己的技能
    assert (home_skill / "SKILL.md").is_file()


def test_unshare_reports_when_not_shared(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_cmd, "SCRIPT_DIR", tmp_path)
    assert share_cmd.run_unshare("missing") == 1


def test_unshare_clears_every_target_by_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_cmd, "SCRIPT_DIR", tmp_path)
    _make_shared(tmp_path, "both", "demo")
    _make_shared(tmp_path, "codex", "demo")

    assert share_cmd.run_unshare("demo") == 0
    assert share_cmd.shared_copies("demo") == []


def test_unshare_can_target_one_tool(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_cmd, "SCRIPT_DIR", tmp_path)
    _make_shared(tmp_path, "both", "demo")
    _make_shared(tmp_path, "codex", "demo")

    assert share_cmd.run_unshare("demo", "codex") == 0
    assert [t for t, _ in share_cmd.shared_copies("demo")] == ["both"]


def test_unshare_rejects_an_unknown_target(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_cmd, "SCRIPT_DIR", tmp_path)
    _make_shared(tmp_path, "both", "demo")

    assert share_cmd.run_unshare("demo", "elsewhere") == 1
    assert (tmp_path / "claude/shared/both/demo").exists()


def test_unshare_is_the_inverse_of_share(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_cmd, "SCRIPT_DIR", tmp_path)
    source = tmp_path / "home" / "skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    monkeypatch.setattr(share_cmd, "CLAUDE_HOME", tmp_path / "home")

    assert share_cmd.run_share("demo") == 0
    assert (tmp_path / "claude/shared/both/demo/SKILL.md").is_file()
    assert share_cmd.run_unshare("demo") == 0
    assert not (tmp_path / "claude/shared/both/demo").exists()
    assert (source / "SKILL.md").is_file()
