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

    # tilde() 會把家目錄縮成 ~,而 Windows 的 tmp_path 就在家目錄底下,
    # 所以比對結尾片段而不是完整路徑
    tail = store.name
    path_lines = [line for line in text.splitlines() if line.strip().endswith(f"{tail}/")]
    assert len(path_lines) == 1


def test_acknowledged_skills_stop_being_reported(tmp_path: Path) -> None:
    from ai_config.skills import acknowledge_unmanaged, unmanaged_skills

    store = tmp_path / "skills"
    _make_unmanaged(store, ["docx", "pdf", "xlsx"])

    assert unmanaged_skills(store) == ["docx", "pdf", "xlsx"]
    assert acknowledge_unmanaged(store) == ["docx", "pdf", "xlsx"]
    assert unmanaged_skills(store) == []


def test_a_newly_shipped_skill_is_still_reported(tmp_path: Path) -> None:
    from ai_config.skills import acknowledge_unmanaged, unmanaged_skills

    store = tmp_path / "skills"
    _make_unmanaged(store, ["docx"])
    acknowledge_unmanaged(store)

    # 工具更新後多出來的技能要提醒一次,使用者才能再決定
    (store / "brand-new").mkdir()
    (store / "brand-new" / "SKILL.md").write_text("x", encoding="utf-8")

    assert unmanaged_skills(store) == ["brand-new"]


def test_acknowledging_twice_keeps_earlier_names(tmp_path: Path) -> None:
    from ai_config.skills import acknowledge_unmanaged, acknowledged_skills

    store = tmp_path / "skills"
    _make_unmanaged(store, ["docx"])
    acknowledge_unmanaged(store)
    (store / "later").mkdir()
    (store / "later" / "SKILL.md").write_text("x", encoding="utf-8")
    acknowledge_unmanaged(store)

    assert acknowledged_skills(store) == {"docx", "later"}


def test_acknowledge_is_a_noop_without_unmanaged_skills(tmp_path: Path) -> None:
    from ai_config.skills import acknowledge_unmanaged

    store = tmp_path / "skills"
    _make_unmanaged(store, [])
    assert acknowledge_unmanaged(store) == []


def test_first_apply_treats_existing_skills_as_known(tmp_path: Path) -> None:
    from ai_config.skills import reconcile_managed_skills, unmanaged_skills

    staged = tmp_path / "staged"
    live = tmp_path / "live"
    staged.mkdir()
    live.mkdir()
    # 工具自帶的技能,在 acg 接管之前就存在
    for name in ["docx", "pdf", "xlsx"]:
        (live / name).mkdir()
        (live / name / "SKILL.md").write_text("x", encoding="utf-8")
    (staged / "acg").mkdir()
    (staged / "acg" / "SKILL.md").write_text("x", encoding="utf-8")

    reconcile_managed_skills(staged, live)

    # 沒有欄位能分辨官方或自寫,但先前就存在的一律視為已知,預設安靜
    assert unmanaged_skills(live) == []


def test_skills_added_after_the_baseline_are_reported(tmp_path: Path) -> None:
    from ai_config.skills import reconcile_managed_skills, unmanaged_skills

    staged = tmp_path / "staged"
    live = tmp_path / "live"
    staged.mkdir()
    live.mkdir()
    (live / "docx").mkdir()
    (live / "docx" / "SKILL.md").write_text("x", encoding="utf-8")
    (staged / "acg").mkdir()
    (staged / "acg" / "SKILL.md").write_text("x", encoding="utf-8")
    reconcile_managed_skills(staged, live)

    (live / "brand-new").mkdir()
    (live / "brand-new" / "SKILL.md").write_text("x", encoding="utf-8")
    reconcile_managed_skills(staged, live)

    assert unmanaged_skills(live) == ["brand-new"]


def test_baseline_does_not_swallow_skills_acg_deploys(tmp_path: Path) -> None:
    from ai_config.skills import acknowledged_skills, reconcile_managed_skills

    staged = tmp_path / "staged"
    live = tmp_path / "live"
    staged.mkdir()
    live.mkdir()
    for name in ["acg", "docx"]:
        (live / name).mkdir()
        (live / name / "SKILL.md").write_text("x", encoding="utf-8")
    (staged / "acg").mkdir()
    (staged / "acg" / "SKILL.md").write_text("x", encoding="utf-8")

    reconcile_managed_skills(staged, live)

    # acg 自己部署的不該被記成「工具自帶」
    assert acknowledged_skills(live) == {"docx"}
