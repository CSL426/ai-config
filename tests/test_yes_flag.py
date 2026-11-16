"""--yes 是給沒有終端機的 agent 用的確認;--force 是同一件事的舊名。"""

import pytest

from ai_config import cli_dispatch, console


@pytest.mark.parametrize("flag", ["--yes", "-y", "--force", "-f"])
def test_every_name_answers_yes_and_is_removed_before_dispatch(
    flag: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list = []
    monkeypatch.setattr(console, "_FORCE", False)
    monkeypatch.setattr(cli_dispatch, "show_status", lambda tool: seen.append(tool) or 0)

    cli_dispatch.main(["status", "claude", flag], usage=lambda: None)

    assert console.forced()
    assert seen == ["claude"]


def test_a_question_kept_from_yes_still_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console, "_FORCE", True)
    monkeypatch.setattr(console, "ask", lambda prompt: None)
    monkeypatch.setattr(console.sys.stdin, "isatty", lambda: False, raising=False)

    assert console.confirm("wipe? ", forceable=False) is False


def test_the_no_terminal_hint_names_yes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(console, "_FORCE", False)
    monkeypatch.setattr(console, "ask", lambda prompt: None)
    monkeypatch.setattr(console.sys.stdin, "isatty", lambda: False, raising=False)

    console.confirm_publish("push? ")

    out = capsys.readouterr().out
    assert "--yes" in out and "--force" not in out


def test_a_second_push_says_another_one_is_running(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """2026-11-13: one push waited at [y/N] while another tripped over what it had staged."""
    from ai_config import locking
    from ai_config.commands import push

    monkeypatch.setattr(locking, "BACKUP_BASE", tmp_path)
    called: list = []
    monkeypatch.setattr(push, "_do_push", lambda *a: called.append(a) or 0)

    with locking.exclusive_lock(".ai-config-push.lock") as held:
        assert held
        assert push.do_push("claude") == 1

    assert not called
    assert "另一個 acg push 正在進行" in capsys.readouterr().err
    assert push.do_push("claude") == 0
