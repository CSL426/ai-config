"""Updating swaps a link, so the file that is running is never replaced."""

import os
from pathlib import Path

import pytest

from ai_config import versions


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AI_CONFIG_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("AI_CONFIG_SHARE_DIR", str(tmp_path / "share"))
    (tmp_path / "bin").mkdir()
    return tmp_path


def _release(tmp_path: Path, body: str) -> Path:
    source = tmp_path / f"download-{body}"
    source.write_text(body, encoding="utf-8")
    return source


def test_a_version_lands_in_its_own_directory(layout: Path) -> None:
    versions.place("1.0.64", _release(layout, "sixtyfour"))

    assert versions.version_binary("1.0.64").read_text() == "sixtyfour"
    assert versions.installed_versions() == ["1.0.64"]
    assert versions.active_version() is None  # 放好了但還沒啟用


def test_activate_points_the_stable_path_at_it(layout: Path) -> None:
    versions.place("1.0.64", _release(layout, "sixtyfour"))

    assert versions.activate("1.0.64") is True

    assert versions.stable_path().read_text() == "sixtyfour"
    assert versions.active_version() == "1.0.64"
    assert os.access(versions.stable_path(), os.X_OK)


def test_the_running_file_is_never_rewritten(layout: Path) -> None:
    """The whole point: a process already running keeps a readable binary.

    Ask the version directory directly rather than resolving the stable
    path: on Windows that path is the copy, which resolves to itself and
    would make this assertion about the wrong file.
    """
    versions.place("1.0.63", _release(layout, "sixtythree"))
    versions.activate("1.0.63")
    running = versions.version_binary("1.0.63")

    versions.place("1.0.64", _release(layout, "sixtyfour"))
    versions.activate("1.0.64")

    assert running.read_text() == "sixtythree"
    assert versions.stable_path().read_text() == "sixtyfour"


def test_going_back_is_one_move(layout: Path) -> None:
    for name, body in (("1.0.63", "sixtythree"), ("1.0.64", "sixtyfour")):
        versions.place(name, _release(layout, body))
    versions.activate("1.0.64")

    versions.activate("1.0.63")

    assert versions.stable_path().read_text() == "sixtythree"
    assert versions.active_version() == "1.0.63"


def test_versions_sort_numerically_not_as_text(layout: Path) -> None:
    for name in ("1.0.9", "1.0.10", "1.0.64"):
        versions.place(name, _release(layout, name))

    assert versions.installed_versions() == ["1.0.9", "1.0.10", "1.0.64"]


def test_prune_keeps_the_newest_and_never_the_active_one(layout: Path) -> None:
    for name in ("1.0.60", "1.0.61", "1.0.62", "1.0.63", "1.0.64"):
        versions.place(name, _release(layout, name))
    versions.activate("1.0.60")

    removed = versions.prune(keep=3)

    assert versions.active_version() == "1.0.60"
    assert versions.version_binary("1.0.60").is_file()
    assert set(removed) == {"1.0.61", "1.0.62"}
    assert versions.installed_versions() == ["1.0.60", "1.0.63", "1.0.64"]


def test_an_older_install_is_adopted_in_place(layout: Path) -> None:
    """A machine installed before this layout has the real file on PATH."""
    plain = versions.stable_path()
    plain.write_text("sixtythree", encoding="utf-8")
    plain.chmod(0o755)

    assert versions.adopt_existing("1.0.63") is True

    assert versions.active_version() == "1.0.63"
    assert versions.stable_path().read_text() == "sixtythree"
    assert versions.version_binary("1.0.63").read_text() == "sixtythree"
    # 已經是新格局了就不再動它
    assert versions.adopt_existing("1.0.63") is False


def test_activating_something_absent_is_refused(layout: Path) -> None:
    with pytest.raises(FileNotFoundError):
        versions.activate("1.0.99")


def test_switching_to_a_version_already_on_disk_needs_no_download(
    layout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Going back is a link move; update must not reach the network for it."""
    import sys

    from ai_config.commands import update

    for name, body in (("1.0.63", "sixtythree"), ("1.0.64", "sixtyfour")):
        versions.place(name, _release(layout, body))
    versions.activate("1.0.64")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(update, "current_version", lambda: "1.0.64")
    monkeypatch.setattr(
        update, "_latest_release_version",
        lambda: pytest.fail("a rollback must not ask the network"),
    )
    monkeypatch.setattr(
        update, "_warn_if_updating_a_different_copy", lambda: None,
    )

    assert update.run_update("1.0.63") == 0

    assert versions.active_version() == "1.0.63"
    assert versions.stable_path().read_text() == "sixtythree"
    assert "1.0.63" in capsys.readouterr().out


def test_windows_keeps_a_copy_and_still_knows_which_version_it_is(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows may not be allowed a symlink; the copy must still be identifiable.

    Every version assertion here failed on the Windows runners until
    activate recorded the name: a copied file resolves to itself and says
    nothing about where it came from.
    """
    monkeypatch.setattr(versions, "NATIVE_WINDOWS", True)
    versions.place("1.0.63", _release(layout, "sixtythree"))
    versions.place("1.0.64", _release(layout, "sixtyfour"))

    versions.activate("1.0.64")

    assert versions.stable_path().is_file()
    assert not versions.stable_path().is_symlink()
    assert versions.stable_path().read_text() == "sixtyfour"
    assert versions.active_version() == "1.0.64"

    versions.activate("1.0.63")

    assert versions.active_version() == "1.0.63"
    assert versions.stable_path().read_text() == "sixtythree"
    # 版本目錄裡的那份從頭到尾沒被動過
    assert versions.version_binary("1.0.64").read_text() == "sixtyfour"


def test_a_record_pointing_at_a_deleted_version_is_ignored(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(versions, "NATIVE_WINDOWS", True)
    versions.place("1.0.64", _release(layout, "sixtyfour"))
    versions.activate("1.0.64")

    import shutil as shutil_mod
    shutil_mod.rmtree(versions.version_dir("1.0.64"))

    assert versions.active_version() is None
