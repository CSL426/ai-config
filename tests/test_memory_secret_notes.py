"""憑證檢查從 push 前移到 status:筆記還沒被暫存就該說。"""

import subprocess
from pathlib import Path

import pytest

from ai_config import memory_index, memory_paths


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # 掃描範圍由 git 決定,所以要真的 repo 和資料庫那份 .gitignore
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    root = tmp_path / "memory"
    (root / memory_paths.TOPICS_NAME).mkdir(parents=True)
    (root / ".gitignore").write_text(f"/{memory_paths.JOURNAL_DIR_NAME}/\n", encoding="utf-8")
    monkeypatch.setattr(memory_paths, "memory_dir", lambda: root)
    return root


def test_a_note_carrying_a_token_is_reported(notebook: Path) -> None:
    note = notebook / memory_paths.TOPICS_NAME / "deploy.md"
    note.write_text("# 部署\n\nghp_" + "aB3" * 10 + "\n", encoding="utf-8")

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


def test_a_project_journal_is_scanned(notebook: Path) -> None:
    """Project journals sync; skipping them hid DESKTOP-CAO4ENQ's held note from status."""
    journal = notebook / memory_paths.PROJECTS_NAME / "o--r" / memory_paths.JOURNAL_DIR_NAME
    journal.mkdir(parents=True)
    (journal / "recent.md").write_text("password=" + "hunter2\n", encoding="utf-8")

    assert memory_index.secret_notes() == [
        f"{memory_paths.PROJECTS_NAME}/o--r/{memory_paths.JOURNAL_DIR_NAME}/recent.md"
    ]


def test_a_handoff_is_scanned(notebook: Path) -> None:
    handoff = notebook / "handoff"
    handoff.mkdir()
    (handoff / "acg 收尾.md").write_text("token=" + "abc123\n", encoding="utf-8")

    assert memory_index.secret_notes() == ["handoff/acg 收尾.md"]


def test_without_git_nothing_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 不在 git 底下就不會同步,也就沒有東西會外洩
    root = tmp_path / "memory"
    root.mkdir()
    (root / memory_paths.INDEX_NAME).write_text("token=" + "abc123\n", encoding="utf-8")
    monkeypatch.setattr(memory_paths, "memory_dir", lambda: root)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

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
    # 只提到參數名、後面沒有值:2026-10-14、15 兩晚的自動上傳都被這種寫法擋下
    "- 取消用 `?token=` 網址參數傳 token",
    "唯讀調查 `?token=` 經 log 外洩風險",
    "api_key=, token=)",
    "?token=&page=2",
    "- 取消用 ?token= 網址參數傳 token(app/auth.py)",
    # 中文句子裡提到 key、後面也接中文:2026-10-21 一份交接因此整份留在本機
    "使用者確認 CAO4ENQ 那份 10/19 日誌的 token:真的就刪值並撤換",
    "改用環境變數的password: 不再寫進設定檔",
    # 單字裡剛好有 sk-、ghp_ 這幾個字:記憶檔名 ask-peer-session-for-windows 曾被當成金鑰
    "見 memory:ask-peer-session-for-windows",
    "the task-runner-configuration-for-windows",
    "naghp_" + "a" * 24,
    "under_sk-peer-session-for-windows",
    "見記憶ask-peer-session-for-windows",
    # markdown 行內程式碼裡的 key:冒號後面就是收尾的反引號
    "loosen cred rule (`token:` after 中文 desc not cred; line-start `password:` still blocked)",
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
    "GH_TOKEN=$(gh auth token) ghp_" + "aB3" * 10,
    '"token": "' + "abc123" + '"',  # JSON 的值以引號開頭,不能當成空值
    "token = " + "abc123",
    "`token=" + "abc123`",
    # 只有 URL/shell 的 key= 才看值是不是空的;JSON、YAML 的值可能在下一行或是區塊
    '"token":\n  "' + "abc123" + '"',
    "token: |\n  " + "abc123",
    'token=""' + "abc123",
    "token=" + "中文abc123",
    "password: " + "中文密碼",
    # 句子裡的 key 後面接的是英數,照樣是值
    "日誌的 token: " + "abc123",
    "見設定 password=" + "hunter2",
    "說明\n" + "token: 中文密碼",
    # token 前面是標點或空白時照樣抓
    "key: sk-" + "aB3" * 8,
    "OPENAI=sk-proj-" + "b7" * 12,
    "(ghp_" + "cQ" * 12 + ")",
    # 黏在單字後面的真 token 也要抓
    "mykeysk-" + "Ab9" * 8,
    "tokenghp_" + "Zz1" * 8,
    "`AKIA" + "D" * 16 + "`",
    # 反引號包著的值照樣是值
    "token: `" + "abc123`",
    "`token:" + "abc123`",
    "token:`" + "abc123`",
    "GitHub token:`" + "abc123`",
])
def test_a_literal_value_is_still_a_credential(line: str) -> None:
    from ai_config.safety import looks_like_secret

    assert looks_like_secret(line)


@pytest.mark.parametrize("line", [
    "password=<hunter2",  # 沒收尾的不是佔位符
    "token=<" + "a" * 60 + ">",  # 太長的尖括號可能包著真值
])
def test_only_a_short_closed_placeholder_is_let_through(line: str) -> None:
    from ai_config.safety import looks_like_secret

    assert looks_like_secret(line)


def test_when_git_cannot_answer_every_note_is_scanned(
    notebook: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A git timeout must not turn into "no credentials"; only the root journal stays out."""
    (notebook / memory_paths.TOPICS_NAME / "deploy.md").write_text("token=" + "abc123\n", encoding="utf-8")
    journal = notebook / memory_paths.JOURNAL_DIR_NAME
    journal.mkdir()
    (journal / "today.md").write_text("token=" + "abc123\n", encoding="utf-8")
    monkeypatch.setattr(memory_index, "_synced_files", lambda root: None)

    assert memory_index.secret_notes() == [f"{memory_paths.TOPICS_NAME}/deploy.md"]
