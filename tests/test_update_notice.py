"""A release notice that can be accepted where it is shown."""

import json
import time
from pathlib import Path

import pytest

from ai_config.commands import update


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "update-check.json"
    monkeypatch.setattr(update, "_update_check_cache_path", lambda: path)
    monkeypatch.setattr(update, "current_version", lambda: "1.0.63")
    monkeypatch.setattr(update, "_spawn_update_check", lambda: None)
    # maybe_notify_update 在測試中會自己退出;這些測試針對它底下的決策
    monkeypatch.delenv("AI_CONFIG_NO_UPDATE_CHECK", raising=False)
    return path


def _write(path: Path, latest: str, age_seconds: float = 0) -> None:
    path.write_text(json.dumps({
        "latest": latest, "checked_at": time.time() - age_seconds,
    }), encoding="utf-8")


def test_yes_updates_without_a_second_command(
    cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = []
    monkeypatch.setattr(update, "confirm", lambda prompt, **kw: asked.append(prompt) or True)
    monkeypatch.setattr(update, "run_update", lambda version: asked.append(version) or 0)

    update._offer_update("1.0.64", "1.0.63")

    assert asked[-1] == "1.0.64", "答應了就該直接更新,不是叫人再跑一次指令"


def test_no_leaves_the_machine_alone(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(update, "confirm", lambda prompt, **kw: False)
    monkeypatch.setattr(
        update, "run_update", lambda v: pytest.fail("declined means nothing happens"),
    )

    update._offer_update("1.0.64", "1.0.63")

    assert "update" in capsys.readouterr().out


def test_the_prompt_is_never_answered_by_force(
    cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--force answers questions about the user's own files, not about this."""
    seen = {}
    monkeypatch.setattr(
        update, "confirm",
        lambda prompt, **kw: seen.update(kw) or False,
    )

    update._offer_update("1.0.64", "1.0.63")

    assert seen.get("forceable") is False


def test_an_interrupted_prompt_is_not_a_yes(
    cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(prompt, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(update, "confirm", interrupt)
    monkeypatch.setattr(
        update, "run_update", lambda v: pytest.fail("Ctrl+C must not update"),
    )

    update._offer_update("1.0.64", "1.0.63")


def test_a_current_machine_is_not_asked_anything(
    cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(cache, "1.0.63")
    monkeypatch.setattr(
        update, "_offer_update", lambda *a: pytest.fail("already current"),
    )
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    update.maybe_notify_update()


def test_the_check_never_reaches_the_network_itself(
    cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The notice reads a cache; refreshing it is someone else's background job."""
    _write(cache, "1.0.64", age_seconds=0)
    monkeypatch.setattr(
        update, "_latest_release_version",
        lambda: pytest.fail("the notice must not block on the network"),
    )
    offered = []
    monkeypatch.setattr(update, "_offer_update", lambda *a: offered.append(a))
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    update.maybe_notify_update()

    assert offered == [("1.0.64", "1.0.63")]
