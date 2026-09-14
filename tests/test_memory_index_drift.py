"""索引是手寫的,摘要無法重建;漂移只能回報,不能自動修。"""

from pathlib import Path

from ai_config import memory


def _notebook(root: Path, index: str, notes: dict[str, str]) -> Path:
    topics = root / memory.TOPICS_NAME
    topics.mkdir(parents=True)
    for name, body in notes.items():
        (topics / name).write_text(body, encoding="utf-8")
    path = root / memory.INDEX_NAME
    path.write_text(index, encoding="utf-8")
    return path


def test_reports_a_note_no_index_line_points_at(tmp_path: Path) -> None:
    index = _notebook(
        tmp_path,
        "# 共用記憶\n\n- [機器](topics/machines.md) — 主機清單\n",
        {"machines.md": "# 機器\n", "gotchas.md": "# 陷阱\n"},
    )

    unlisted, dangling = memory.index_drift(index, memory.TOPICS_NAME)

    assert unlisted == ["gotchas.md"]
    assert dangling == []


def test_reports_an_index_line_pointing_at_nothing(tmp_path: Path) -> None:
    index = _notebook(
        tmp_path,
        "# 共用記憶\n\n- [已刪](topics/removed.md) — 不在了\n",
        {},
    )

    unlisted, dangling = memory.index_drift(index, memory.TOPICS_NAME)

    assert unlisted == []
    assert dangling == ["removed.md"]


def test_external_links_are_not_dangling(tmp_path: Path) -> None:
    index = _notebook(
        tmp_path,
        "# 共用記憶\n\n- [文件](https://example.com/x.md) — 外部連結\n",
        {},
    )

    assert memory.index_drift(index, memory.TOPICS_NAME) == ([], [])


def test_a_tidy_notebook_reports_nothing(tmp_path: Path) -> None:
    index = _notebook(
        tmp_path,
        "# 共用記憶\n\n- [機器](topics/machines.md) — 主機清單\n",
        {"machines.md": "# 機器\n"},
    )

    assert memory.index_drift(index, memory.TOPICS_NAME) == ([], [])


def test_missing_index_is_not_drift(tmp_path: Path) -> None:
    (tmp_path / memory.TOPICS_NAME).mkdir()
    (tmp_path / memory.TOPICS_NAME / "orphan.md").write_text("x\n")

    assert memory.index_drift(tmp_path / memory.INDEX_NAME, memory.TOPICS_NAME) == (
        [],
        [],
    )
