"""交接是每條工作線各一份;日誌解決不了「我這條線做到哪」。"""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from ai_config import handoff, memory


def _rewrite(path: Path, edit: "Callable[[str], str]") -> None:
    """Edit a note the way the module would, keeping its line endings.

    Path.write_text translates "\\n" to CRLF on Windows, and the field
    regex is anchored with $, so a stray "\\r" makes every field read as
    missing and the note parses as if it were empty.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(edit(text))


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

    note, _ = handoff.claim("記憶改善")

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

    assert handoff.claim("記憶改善")[0].claimed_by == "session-one"


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


def test_created_survives_a_rewrite(notebook: Path) -> None:
    """updated moves with every touch, so alone it cannot say how old a thread is.

    A thread sat in the list for three days; the only timestamp on it
    was the one `done` had just rewritten.
    """
    first = handoff.write("長命線", "第一版").created

    handoff.claim("長命線")
    handoff.write("長命線", "第二版")
    note = handoff.done("長命線")

    # 時間戳只到秒,同一秒內跑完的話 updated 會等於 created;
    # 這裡要問的是 created 有沒有被後續的寫入蓋掉
    assert note.created == first
    assert note.updated >= first


def test_an_older_note_without_created_falls_back(notebook: Path) -> None:
    """Notes written before the field exists must still load."""
    handoff.write("舊的", "內容")
    path = handoff.handoff_dir() / "舊的.md"
    _rewrite(path, lambda text: text.replace("created: ", "legacy: ", 1))

    note = handoff.load_all()[0]

    assert note.created == note.updated


def test_the_list_says_how_long_a_thread_has_waited(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """"Opened three days ago" is the reason to pick it up; updated cannot say it."""
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("放很久的", "內容")
    path = handoff.handoff_dir() / "放很久的.md"
    _rewrite(path, lambda text: text.replace("created: 2026", "created: 2020", 1))

    assert command.run_memory(["handoff", "list"]) == 0

    assert "天前" in capsys.readouterr().out


def test_a_note_with_windows_line_endings_still_loads(notebook: Path) -> None:
    """The directory syncs to a Windows machine, which can hand back CRLF.

    The field regex anchors on $, so a trailing "\\r" left every field
    reading as missing: the note vanished from the list and claiming it
    reported the file as malformed.
    """
    handoff.write("跨平台線", "內容")
    path = handoff.handoff_dir() / "跨平台線.md"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    notes = handoff.load_all()

    assert [n.thread for n in notes] == ["跨平台線"]
    assert notes[0].state == handoff.OPEN
    assert notes[0].body == "內容"


def test_free_text_is_still_accepted(notebook: Path) -> None:
    """The template is a suggestion; seven notes predate it."""
    note = handoff.write("舊式的", "就是一段話,沒有標題")

    assert note.body == "就是一段話,沒有標題"


def test_the_list_surfaces_what_is_left(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Next is the line a session picking this up needs first."""
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("有格式的", "## Goal\n做完 X\n\n## Next\n- 補上聲紋註冊\n- 調 bitrate")

    assert command.run_memory(["handoff", "list"]) == 0

    listed = capsys.readouterr().out
    assert "補上聲紋註冊" in listed


def test_a_note_without_headings_lists_as_before(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("沒標題的", "第一行就是摘要\n後面還有別的")

    assert command.run_memory(["handoff", "list"]) == 0

    assert "第一行就是摘要" in capsys.readouterr().out


def _age_note(path: Path, field: str, days: int) -> None:
    """Backdate one timestamp field, the way a note left sitting would look."""
    from datetime import UTC, datetime, timedelta

    stamp = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _rewrite(path, lambda text: re.sub(
        rf"^{field}: .*$", f"{field}: {stamp}", text, count=1, flags=re.MULTILINE
    ))


def test_a_claim_left_sitting_can_be_taken_over(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim is a status line, not a lock, but nothing ever released one.

    Sessions end without running `done`: the terminal closes, the context
    runs out. The note keeps a holder that no longer exists, and every
    later session is refused by a session id that died days ago.
    """
    handoff.write("卡住的", "內容")
    handoff.claim("卡住的")
    _age_note(handoff.handoff_dir() / "卡住的.md", "updated", 2)

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-two")
    note, _ = handoff.claim("卡住的")

    assert note.claimed_by == "session-two"


def test_a_fresh_claim_is_still_protected(
    notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timing out a stale claim must not open up a live one."""
    handoff.write("有人在做", "內容")
    handoff.claim("有人在做")

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-two")
    with pytest.raises(ValueError, match="已被其他 session 認領"):
        handoff.claim("有人在做")


def test_a_stale_claim_says_who_held_it(
    notebook: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Taking over silently hides that someone else was on this thread."""
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("卡住的", "內容")
    handoff.claim("卡住的")
    _age_note(handoff.handoff_dir() / "卡住的.md", "updated", 2)

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-two")
    assert command.run_memory(["handoff", "claim", "卡住的"]) == 0

    told = capsys.readouterr().out
    # 前持有者印的是縮寫,不是整串 id;要的是接手的人看得出有人在過
    assert handoff.short_id("session-one") in told
    assert "已接手" in told


def test_a_thread_nobody_touched_is_flagged_as_stale(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An abandoned thread lists exactly like one written an hour ago.

    One sat open for four days while the work in it shipped elsewhere;
    it still read as pickup work, and the version it was waiting on was
    fifteen releases back.
    """
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("放很久的", "內容")
    _age_note(handoff.handoff_dir() / "放很久的.md", "updated", 4)

    assert command.run_memory(["handoff", "list"]) == 0

    assert "可能已過期" in capsys.readouterr().out


def test_a_thread_touched_today_is_not_flagged(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("剛寫的", "內容")

    assert command.run_memory(["handoff", "list"]) == 0

    assert "可能已過期" not in capsys.readouterr().out


def test_a_shortened_id_does_not_end_mid_segment() -> None:
    """A blind slice left "session-", which reads as a truncated word.

    Real ids are uuids and cut cleanly at the first segment; the stub
    only showed up on the shorter ids used in tests and logs.
    """
    assert handoff.short_id("d4b49a91-38e2-4f31-900a-24d0ae63b153") == "d4b49a91"
    assert handoff.short_id("session-one") == "session"
    assert handoff.short_id("abc") == "abc"


def test_a_thread_done_long_enough_is_archived(notebook: Path) -> None:
    """Finished threads stay as a record, but not in the working directory.

    They are never listed again, so they only make the directory harder
    to scan and slower to load. Archiving keeps the record and takes it
    out of the way.
    """
    handoff.write("做完很久的", "內容")
    handoff.done("做完很久的")
    _age_note(handoff.handoff_dir() / "做完很久的.md", "updated", 40)

    moved = handoff.archive_finished()

    assert moved == ["做完很久的"]
    assert not (handoff.handoff_dir() / "做完很久的.md").exists()
    assert (handoff.archive_dir() / "做完很久的.md").is_file()


def test_a_recently_finished_thread_stays_put(notebook: Path) -> None:
    """Closing a thread and changing your mind happens the same day."""
    handoff.write("剛做完的", "內容")
    handoff.done("剛做完的")

    assert handoff.archive_finished() == []
    assert (handoff.handoff_dir() / "剛做完的.md").is_file()


def test_open_and_claimed_threads_are_never_archived(notebook: Path) -> None:
    """Only a thread someone closed is finished; age alone means nothing."""
    handoff.write("放著沒做的", "內容")
    _age_note(handoff.handoff_dir() / "放著沒做的.md", "updated", 90)
    handoff.write("認領著的", "內容")
    handoff.claim("認領著的")
    _age_note(handoff.handoff_dir() / "認領著的.md", "updated", 90)

    assert handoff.archive_finished() == []
    assert len(list(handoff.handoff_dir().glob("*.md"))) == 2


def test_an_archived_thread_leaves_the_listing(notebook: Path) -> None:
    """load_all reads the working directory, so archiving must remove it there."""
    handoff.write("歸檔的", "內容")
    handoff.done("歸檔的")
    _age_note(handoff.handoff_dir() / "歸檔的.md", "updated", 40)

    handoff.archive_finished()

    assert handoff.load_all() == []


def test_archiving_does_not_overwrite_an_older_record(notebook: Path) -> None:
    """A thread name can come back; the archived record must survive it.

    Threads are named by hand and reused ("收尾", "release"), so a
    straight move would silently drop whichever record moved first.
    """
    handoff.write("同名的", "第一次")
    handoff.done("同名的")
    _age_note(handoff.handoff_dir() / "同名的.md", "updated", 40)
    handoff.archive_finished()

    handoff.write("同名的", "第二次")
    handoff.done("同名的")
    _age_note(handoff.handoff_dir() / "同名的.md", "updated", 40)
    handoff.archive_finished()

    kept = sorted(p.read_text(encoding="utf-8") for p in handoff.archive_dir().glob("*.md"))
    assert len(kept) == 2
    assert any("第一次" in text for text in kept)
    assert any("第二次" in text for text in kept)


def test_listing_archives_what_is_long_finished(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Archiving has to run by itself; nobody remembers a cleanup command."""
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("陳年舊事", "內容")
    handoff.done("陳年舊事")
    _age_note(handoff.handoff_dir() / "陳年舊事.md", "updated", 40)
    handoff.write("還在做的", "內容")

    assert command.run_memory(["handoff", "list"]) == 0

    capsys.readouterr()
    assert (handoff.archive_dir() / "陳年舊事.md").is_file()
    assert not (handoff.handoff_dir() / "陳年舊事.md").exists()


def test_a_broken_archive_does_not_break_the_listing(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The list is what the session needs; tidying failing must not hide it."""
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )

    def refuse(*args: object, **kwargs: object) -> list[str]:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(handoff, "archive_finished", refuse)
    handoff.write("還在做的", "進度")

    assert command.run_memory(["handoff", "list"]) == 0

    assert "還在做的" in capsys.readouterr().out


def test_archiving_says_what_it_moved(
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """These files are versioned, so a silent move becomes an unexplained rename."""
    from ai_config.commands import memory as command

    monkeypatch.setattr(
        memory, "project_key", lambda cwd=None: memory.ProjectKey("o--r", True, "t"),
    )
    handoff.write("陳年舊事", "內容")
    handoff.done("陳年舊事")
    _age_note(handoff.handoff_dir() / "陳年舊事.md", "updated", 40)
    handoff.write("還在做的", "內容")

    assert command.run_memory(["handoff", "list"]) == 0

    assert "陳年舊事" in capsys.readouterr().out


def test_the_summary_does_not_keep_half_a_bold_marker() -> None:
    """Next items are written as "1. **Do X**。rest"; the list showed "Do X**".

    Only the leading asterisks were stripped, so every bolded item ended
    in a stray "**" — reported from the openVman session's list.
    """
    body = "## Next\n1. **優先：push 並開 PR。** 五個 commit 還在本機。\n"

    line = handoff.summary(body)

    assert "**" not in line
    assert "優先：push 並開 PR" in line


def test_the_fallback_summary_skips_headings() -> None:
    """A note opening with its own heading listed as "## 這條線在做什麼".

    Without a Next or Unknowns section the list falls back to the first
    line, and the first line was the heading — which says nothing.
    """
    body = "## 這條線在做什麼\n\n追 MVP114 的 KPI 差異\n"

    assert handoff.summary(body) == "追 MVP114 的 KPI 差異"
