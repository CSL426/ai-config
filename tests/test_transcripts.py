"""接手後把寫交接的舊對話移出 /resume 清單,但絕不搬錯、不搬活的。"""

import json
import os
from pathlib import Path

import pytest

from ai_config import handoff, transcripts
from ai_config.commands import memory_handoff

OLD = "3c3c6c6c-9083-4e90-8bf6-37337d1ecd4d"
NEW = "ccea2e13-3656-4d9a-800c-eb02a19e6946"


@pytest.fixture
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude"
    (home / "sessions").mkdir(parents=True)
    monkeypatch.setattr(transcripts, "_claude_home", lambda: home)
    monkeypatch.setattr(handoff, "_sessions_dir", lambda: home / "sessions")
    return home


def _transcript(home: Path, session: str, project: str = "-home-me-repo") -> Path:
    folder = home / "projects" / project
    (folder / session / "subagents").mkdir(parents=True)
    (folder / session / "subagents" / "a.jsonl").write_text("{}", encoding="utf-8")
    path = folder / f"{session}.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    return path


def _running(home: Path, session: str, pid: int = 100) -> None:
    record = {"pid": pid, "sessionId": session, "name": "acg"}
    (home / "sessions" / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")


def test_the_replaced_session_leaves_the_resume_list(claude: Path) -> None:
    source = _transcript(claude, OLD)
    _running(claude, NEW)

    moved, problem = transcripts.retire(OLD, current=NEW)

    archived = claude / "archived-sessions" / "-home-me-repo"
    assert problem == ""
    assert moved == [archived / OLD, archived / f"{OLD}.jsonl"]
    assert not source.exists() and not (source.parent / OLD).exists()
    # 子代理的紀錄跟著搬,找回時整份放回去就能 resume
    assert (archived / OLD / "subagents" / "a.jsonl").is_file()


def test_a_session_still_running_is_left_alone(claude: Path) -> None:
    """A named claim can take another live session's thread; its transcript is in use."""
    source = _transcript(claude, OLD)
    _running(claude, OLD, pid=200)

    assert transcripts.retire(OLD, current=NEW) == ([], "")
    assert source.is_file()


def test_this_session_never_archives_itself(claude: Path) -> None:
    source = _transcript(claude, NEW)

    assert transcripts.retire(NEW, current=NEW) == ([], "")
    assert source.is_file()


@pytest.mark.parametrize("author", ["", "session-one", "../../x", f"{OLD}/..", "*", f"../{OLD}"])
def test_only_a_real_session_id_becomes_a_path(claude: Path, author: str) -> None:
    """The note syncs in from other machines; its author field is not trusted."""
    _transcript(claude, OLD)

    assert transcripts.retire(author) == ([], "")
    assert not (claude / "archived-sessions").exists()


def test_a_note_from_another_machine_has_nothing_to_move(claude: Path) -> None:
    (claude / "projects").mkdir()

    assert transcripts.retire(OLD, current=NEW) == ([], "")


def test_an_existing_archive_is_not_overwritten(claude: Path) -> None:
    source = _transcript(claude, OLD)
    kept = claude / "archived-sessions" / "-home-me-repo" / f"{OLD}.jsonl"
    kept.parent.mkdir(parents=True)
    kept.write_text("earlier\n", encoding="utf-8")

    moved, problem = transcripts.retire(OLD, current=NEW)

    assert moved == [] and "同名" in problem
    assert source.is_file()
    assert kept.read_text(encoding="utf-8") == "earlier\n"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_linked_project_folder_is_not_followed(claude: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / f"{OLD}.jsonl").write_text("{}\n", encoding="utf-8")
    (claude / "projects").mkdir()
    (claude / "projects" / "linked").symlink_to(elsewhere, target_is_directory=True)

    moved, problem = transcripts.retire(OLD, current=NEW)

    assert moved == [] and "連結" in problem
    assert (elsewhere / f"{OLD}.jsonl").is_file()


def test_a_failed_move_is_reported_not_raised(
    claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows the file can be locked; the pickup itself must still succeed."""
    _transcript(claude, OLD)

    def locked(self: Path, target: Path) -> None:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(Path, "rename", locked)

    moved, problem = transcripts.retire(OLD, current=NEW)

    assert moved == [] and "PermissionError" in problem


def test_an_upper_case_id_cannot_reach_a_live_session(claude: Path) -> None:
    """NTFS and APFS match names without case; the checks must not be fooled by it."""
    source = _transcript(claude, OLD)
    _running(claude, OLD, pid=200)

    assert transcripts.retire(OLD.upper(), current=NEW) == ([], "")
    assert transcripts.retire(OLD.upper(), current=OLD) == ([], "")
    assert source.is_file()


def test_a_trailing_newline_is_not_an_id(claude: Path) -> None:
    assert transcripts._session(OLD + "\n") == OLD
    assert transcripts._session(OLD + "\nx") == ""


def test_a_move_cut_short_still_reports_what_moved(
    claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sidecar goes first; if the transcript is then locked, say what did move."""
    source = _transcript(claude, OLD)
    real = Path.rename

    def lock_transcript(self: Path, target: Path) -> Path:
        if self.suffix == ".jsonl":
            raise PermissionError(13, "in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", lock_transcript)

    moved, problem = transcripts.retire(OLD, current=NEW)

    assert moved == [claude / "archived-sessions" / "-home-me-repo" / OLD]
    assert "PermissionError" in problem
    assert source.is_file()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_linked_sidecar_stops_the_move(claude: Path, tmp_path: Path) -> None:
    folder = claude / "projects" / "-home-me-repo"
    folder.mkdir(parents=True)
    source = folder / f"{OLD}.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    (folder / OLD).symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    moved, problem = transcripts.retire(OLD, current=NEW)

    assert moved == [] and "連結" in problem
    assert source.is_file()


def test_a_fresh_transcript_waits_to_settle(claude: Path) -> None:
    source = _transcript(claude, OLD)
    written = source.stat().st_mtime

    assert transcripts.settle_left(OLD, now=written + 10) == pytest.approx(50)
    assert transcripts.settle_left(OLD, now=written + 61) == 0
    assert transcripts.settle_left("nope", now=written) == 0


def test_the_detached_half_moves_after_waiting(
    claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _transcript(claude, OLD)
    slept: list = []
    monkeypatch.setattr(transcripts.time, "sleep", slept.append)

    assert transcripts.run_detached([OLD, NEW]) == 0

    assert 0 < slept[0] <= transcripts.SETTLE_SECONDS
    assert not source.exists()


def test_pickup_archives_the_writer_and_still_prints_the_note(
    claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    from ai_config import memory_paths

    root = tmp_path / "memory"
    root.mkdir()
    monkeypatch.setattr(memory_paths, "memory_dir", lambda: root)
    monkeypatch.setattr(handoff, "memory_dir", lambda: root)
    monkeypatch.setattr(
        handoff, "project_key", lambda cwd=None: memory_paths.ProjectKey("o--r", True, "t")
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OLD)
    _running(claude, OLD)
    handoff.write("排程", "下一步:測試")
    source = _transcript(claude, OLD)
    old = source.stat().st_mtime - 120
    os.utime(source, (old, old))

    # /clear:同一個行程換成新 id
    _running(claude, NEW)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", NEW)
    assert memory_handoff._handoff(["claim", "排程"]) == 0

    assert not source.exists()
    assert "下一步:測試" in capsys.readouterr().out


def test_pickup_right_after_clear_defers_the_move(
    claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """remember reads the old transcript in the background after /clear."""
    _transcript(claude, OLD)
    _running(claude, NEW)
    deferred: list = []
    monkeypatch.setattr(transcripts, "retire_later", lambda *a: deferred.append(a) or True)

    def must_not(*args: object) -> None:
        raise AssertionError("moved while the transcript was still fresh")

    monkeypatch.setattr(transcripts, "retire", must_not)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", NEW)

    memory_handoff._retire_author(OLD)

    assert deferred == [(OLD, NEW)]
    assert "秒後" in capsys.readouterr().out


def test_pickup_of_its_own_note_spawns_nothing(
    claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _transcript(claude, NEW)
    monkeypatch.setattr(transcripts, "retire_later", lambda *a: pytest.fail("spawned"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", NEW)

    memory_handoff._retire_author(NEW)
