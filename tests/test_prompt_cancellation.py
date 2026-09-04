"""Ctrl+C at any prompt cancels cleanly instead of re-asking or crashing."""

import builtins

import pytest

from ai_config import console
from ai_config.commands import maintenance, push, setup


def _interrupt_on_input(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def interrupt(prompt: str = "") -> str:
        seen.append(prompt)
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupt)
    return seen


def _eof_on_input(monkeypatch: pytest.MonkeyPatch) -> None:
    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)


def test_ask_returns_none_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _interrupt_on_input(monkeypatch)
    assert console.ask("pick? ") is None
    assert seen == ["pick? "]


def test_ask_returns_none_on_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    _eof_on_input(monkeypatch)
    assert console.ask("pick? ") is None


def test_confirm_declines_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    _interrupt_on_input(monkeypatch)
    assert console.confirm("sure? [y/N] ") is False


@pytest.mark.parametrize("answer", ["y", "Y", "yes", " yes "])
def test_confirm_accepts_yes(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(builtins, "input", lambda prompt="": answer)
    assert console.confirm("sure? [y/N] ") is True


@pytest.mark.parametrize("answer", ["", "n", "N", "no", "maybe"])
def test_confirm_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr(builtins, "input", lambda prompt="": answer)
    assert console.confirm("sure? [y/N] ") is False


def test_push_confirmation_asks_once_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _interrupt_on_input(monkeypatch)
    assert push._review_and_confirm_push("pending", "diff", "message") is False
    assert len(seen) == 1


def test_reset_cancels_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _interrupt_on_input(monkeypatch)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("reset must not touch the filesystem when cancelled")

    monkeypatch.setattr(maintenance, "reset_tool", unexpected, raising=False)

    assert maintenance.do_reset() is True
    assert len(seen) == 1


def test_setup_prompt_raises_cancelled_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _interrupt_on_input(monkeypatch)
    with pytest.raises(setup.SetupCancelled):
        setup._prompt("Data repository directory", "/tmp/x")


def test_setup_prompt_keeps_default_on_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert setup._prompt("Directory", "/tmp/default") == "/tmp/default"


def test_interactive_setup_exits_quietly_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _interrupt_on_input(monkeypatch)
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True, raising=False)

    assert setup.run_setup([]) == 130
    # 只問一次:取消之後不會再往下追問 provider 或 URL
    assert len(seen) == 1
