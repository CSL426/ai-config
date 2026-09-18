"""交接是每條工作線各一份;日誌解決不了「我這條線做到哪」。"""

from pathlib import Path

import pytest

from ai_config import handoff, memory


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    root.mkdir(parents=True)
    monkeypatch.setattr(memory, "memory_dir", lambda: root)
    monkeypatch.setattr(handoff, "memory_dir", lambda: root)
    monkeypatch.setattr(
        handoff, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t")
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-one")
    return root


def test_a_thread_is_written_and_read_back(notebook: Path) -> None:
    handoff.write("記憶改善", "做到一半,下一步是測試")

    notes = handoff.load_all()

    assert [n.thread for n in notes] == ["記憶改善"]
    assert notes[0].state == handoff.OPEN
    assert notes[0].body == "做到一半,下一步是測試"


def test_chinese_thread_names_stay_distinct(notebook: Path) -> None:
    # 中文若被濾成同一個 fallback,幾條線會互相覆蓋
    handoff.write("記憶改善", "第一條")
    handoff.write("GUI 改版", "第二條")

    assert sorted(n.thread for n in handoff.load_all()) == ["GUI 改版", "記憶改善"]


def test_claiming_marks_the_holder(notebook: Path) -> None:
    handoff.write("記憶改善", "內容")

    note = handoff.claim("記憶改善")

    assert note.state == handoff.CLAIMED
    assert note.claimed_by == "session-one"


def test_another_session_cannot_steal_a_claim(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff.write("記憶改善", "內容")
    handoff.claim("記憶改善")

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-two")
    with pytest.raises(ValueError, match="已被其他 session 認領"):
        handoff.claim("記憶改善")


def test_the_same_session_can_reclaim_its_own(notebook: Path) -> None:
    handoff.write("記憶改善", "內容")
    handoff.claim("記憶改善")

    assert handoff.claim("記憶改善").claimed_by == "session-one"


def test_done_takes_it_off_the_pile(notebook: Path) -> None:
    handoff.write("記憶改善", "內容")

    assert handoff.done("記憶改善").state == handoff.DONE


def test_writing_again_reopens_the_thread(notebook: Path) -> None:
    handoff.write("記憶改善", "第一版")
    handoff.claim("記憶改善")

    note = handoff.write("記憶改善", "第二版")

    assert note.state == handoff.OPEN
    assert note.claimed_by == ""
    assert note.body == "第二版"


def test_empty_thread_or_body_is_refused(notebook: Path) -> None:
    with pytest.raises(ValueError):
        handoff.write("  ", "有內容")
    with pytest.raises(ValueError):
        handoff.write("有名稱", "   ")


def test_claiming_something_absent_says_so(notebook: Path) -> None:
    with pytest.raises(ValueError, match="沒有這則交接"):
        handoff.claim("不存在")


def test_only_this_project_is_listed(notebook: Path) -> None:
    handoff.write("本專案", "內容")

    assert [n.thread for n in handoff.load_all("o--r")] == ["本專案"]
    assert handoff.load_all("別的專案") == []


def test_a_malformed_file_is_skipped_not_fatal(notebook: Path) -> None:
    handoff.handoff_dir().mkdir(parents=True, exist_ok=True)
    (handoff.handoff_dir() / "broken.md").write_text("沒有 frontmatter\n")
    handoff.write("好的", "內容")

    assert [n.thread for n in handoff.load_all()] == ["好的"]


def test_the_hint_quotes_a_name_with_spaces(
    notebook: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 提示是給人照著貼的:名稱有空格卻沒引號,貼上去會被拆成多個參數
    from ai_config.commands import memory as command

    assert command._handoff(["write", "含 空格 的線", "內容"]) == 0
    assert "'含 空格 的線'" in capsys.readouterr().out


def test_listing_takes_another_projects_path(
    notebook: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ai_config.commands import memory as command

    other = tmp_path / "other-project"
    other.mkdir()

    def by_path(cwd: "Path | None" = None) -> memory.ProjectKey:
        name = "other--repo" if cwd == other else "o--r"
        return memory.ProjectKey(name, True, "t")

    monkeypatch.setattr(memory, "project_key", by_path)
    monkeypatch.setattr(handoff, "project_key", by_path)
    handoff.write("本線", "這裡的進度")
    handoff.write("別線", "那邊的進度", cwd=other)

    assert command.run_memory(["handoff", "list"]) == 0
    here = capsys.readouterr().out
    assert command.run_memory(["handoff", "list", str(other)]) == 0
    there = capsys.readouterr().out
    assert "本線" in here and "別線" not in here
    assert "別線" in there and "本線" not in there


def test_listing_a_missing_path_is_refused(notebook: Path, tmp_path: Path) -> None:
    from ai_config.commands import memory as command

    assert command.run_memory(["handoff", "list", str(tmp_path / "nope")]) == 1


def test_a_finished_thread_leaves_the_pickup_list(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """list answers "what can I take", so a closed thread is noise there.

    One sat in the list for three days after being closed, and every
    session had to work out which of the two was still alive.
    """
    from ai_config.commands import memory as command

    # list 問的是 memory.project_key;fixture 只換了 handoff 那邊的
    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("還在做的", "進行中")
    handoff.write("做完的", "已結束")
    handoff.done("做完的")

    assert command.run_memory(["handoff", "list"]) == 0
    listed = capsys.readouterr().out

    assert "還在做的" in listed
    assert "做完的" not in listed
    # 紀錄本身留著,只是不再擋在待接清單裡
    assert {n.thread for n in handoff.load_all("o--r")} == {"還在做的", "做完的"}


def test_a_finished_thread_cannot_be_claimed(notebook: Path) -> None:
    """Claiming a closed thread used to resurrect it into the pickup list.

    A session that misread the list marker went to claim a thread that
    had been finished three days earlier; nothing stopped it, and the
    note came back as live work with the wrong holder on it.
    """
    handoff.write("做完的", "內容")
    handoff.done("做完的")

    with pytest.raises(ValueError, match="已結束"):
        handoff.claim("做完的")

    assert [n.state for n in handoff.load_all()] == [handoff.DONE]
