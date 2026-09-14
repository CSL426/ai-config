"""憑證檢查從 push 前移到 status:筆記還沒被暫存就該說。"""

from pathlib import Path

import pytest

from ai_config import memory


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    (root / memory.TOPICS_NAME).mkdir(parents=True)
    monkeypatch.setattr(memory, "memory_dir", lambda: root)
    return root


def test_a_note_carrying_a_token_is_reported(notebook: Path) -> None:
    note = notebook / memory.TOPICS_NAME / "deploy.md"
    note.write_text("# 部署\n\nghp_" + "a" * 30 + "\n", encoding="utf-8")

    assert memory.secret_notes() == [f"{memory.TOPICS_NAME}/deploy.md"]


def test_ordinary_prose_is_not_a_secret(notebook: Path) -> None:
    note = notebook / memory.TOPICS_NAME / "notes.md"
    note.write_text("# 筆記\n\n今天修好了索引漂移的偵測。\n", encoding="utf-8")

    assert memory.secret_notes() == []


def test_the_local_journal_is_not_scanned(notebook: Path) -> None:
    # journal 被 memory/.gitignore 排除,永遠不會同步出去
    journal = notebook / memory.JOURNAL_DIR_NAME / "slug"
    journal.mkdir(parents=True)
    (journal / "today.md").write_text("API_KEY=abc123\n", encoding="utf-8")

    assert memory.secret_notes() == []


def test_a_project_journal_is_also_skipped(notebook: Path) -> None:
    journal = notebook / memory.PROJECTS_NAME / "o--r" / memory.JOURNAL_DIR_NAME
    journal.mkdir(parents=True)
    (journal / "recent.md").write_text("password=hunter2\n", encoding="utf-8")

    assert memory.secret_notes() == []


def test_a_project_note_is_scanned(notebook: Path) -> None:
    project = notebook / memory.PROJECTS_NAME / "o--r"
    project.mkdir(parents=True)
    (project / memory.INDEX_NAME).write_text(
        "aws_secret_access_key=x\n", encoding="utf-8"
    )

    assert memory.secret_notes() == [
        f"{memory.PROJECTS_NAME}/o--r/{memory.INDEX_NAME}"
    ]
