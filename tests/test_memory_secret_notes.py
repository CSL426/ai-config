"""憑證檢查從 push 前移到 status:筆記還沒被暫存就該說。"""

from pathlib import Path

import pytest

from ai_config import memory_index, memory_paths


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    (root / memory_paths.TOPICS_NAME).mkdir(parents=True)
    monkeypatch.setattr(memory_paths, "memory_dir", lambda: root)
    return root


def test_a_note_carrying_a_token_is_reported(notebook: Path) -> None:
    note = notebook / memory_paths.TOPICS_NAME / "deploy.md"
    note.write_text("# 部署\n\nghp_" + "a" * 30 + "\n", encoding="utf-8")

    assert memory_index.secret_notes() == [f"{memory_paths.TOPICS_NAME}/deploy.md"]


def test_ordinary_prose_is_not_a_secret(notebook: Path) -> None:
    note = notebook / memory_paths.TOPICS_NAME / "notes.md"
    note.write_text("# 筆記\n\n今天修好了索引漂移的偵測。\n", encoding="utf-8")

    assert memory_index.secret_notes() == []


def test_the_local_journal_is_not_scanned(notebook: Path) -> None:
    # journal 被 memory/.gitignore 排除,永遠不會同步出去
    journal = notebook / memory_paths.JOURNAL_DIR_NAME / "slug"
    journal.mkdir(parents=True)
    (journal / "today.md").write_text("API_KEY=abc123\n", encoding="utf-8")

    assert memory_index.secret_notes() == []


def test_a_project_journal_is_also_skipped(notebook: Path) -> None:
    journal = notebook / memory_paths.PROJECTS_NAME / "o--r" / memory_paths.JOURNAL_DIR_NAME
    journal.mkdir(parents=True)
    (journal / "recent.md").write_text("password=hunter2\n", encoding="utf-8")

    assert memory_index.secret_notes() == []


def test_a_project_note_is_scanned(notebook: Path) -> None:
    project = notebook / memory_paths.PROJECTS_NAME / "o--r"
    project.mkdir(parents=True)
    (project / memory_paths.INDEX_NAME).write_text(
        "aws_secret_access_key=x\n", encoding="utf-8"
    )

    assert memory_index.secret_notes() == [
        f"{memory_paths.PROJECTS_NAME}/o--r/{memory_paths.INDEX_NAME}"
    ]


@pytest.mark.parametrize("line", [
    "GH_TOKEN=$(gh auth token --user CSL426) gh pr create",
    "export GITHUB_TOKEN=${TOKEN}",
    'password: "$DB_PASSWORD"',
    "api_key = $API_KEY",
    "指令:`OPENAI_API_KEY=<金鑰> python3 probe.py`",
    'token: "<your token here>"',
])
def test_a_shell_reference_is_not_a_credential(line: str) -> None:
    """A handoff quoting a command blocked a night's autopush on 2026-09-30."""
    from ai_config.safety import looks_like_secret

    assert not looks_like_secret(line)


@pytest.mark.parametrize("line", [
    # 在執行時才組出來:原始碼裡的假憑證字面值會被 gitleaks 擋下
    "GH_TOKEN=" + "abc123def456",
    "password: " + "hunter2",
    "api_key = " + '"' + "sk-live-abc" + '"',
    "token=" + "$",  # 字面上的 $ 結尾不是變數
    "GH_TOKEN=$(gh auth token) ghp_" + "a" * 30,
])
def test_a_literal_value_is_still_a_credential(line: str) -> None:
    from ai_config.safety import looks_like_secret

    assert looks_like_secret(line)
