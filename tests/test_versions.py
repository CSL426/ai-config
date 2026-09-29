"""Updating swaps a link, so the file that is running is never replaced."""

import hashlib
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
    finished = []
    monkeypatch.setattr(update, "_finish_in_installed_version", lambda: finished.append(True))

    assert update.run_update("1.0.63") == 0
    # 換了版本就要由那一版補 plugin 與 hook
    assert finished == [True]

    assert versions.active_version() == "1.0.63"
    assert versions.stable_path().read_text() == "sixtythree"
    assert "1.0.63" in capsys.readouterr().out


def test_windows_launcher_switches_by_the_record_alone(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The launcher on PATH is never rewritten; only versions/active changes.

    The copy it replaced was rewritten on every switch, and a running
    onefile build reading its modules from that path died mid-update.
    """
    monkeypatch.setattr(versions, "NATIVE_WINDOWS", True)
    versions.place("1.0.63", _release(layout, "sixtythree"))
    versions.place("1.0.64", _release(layout, "sixtyfour"))
    versions.stable_path().write_bytes(b"launcher")
    # install.ps1 記下它放上 PATH 的啟動器
    (layout / "share" / "launcher.sha256").write_text(
        hashlib.sha256(b"launcher").hexdigest() + "\n", encoding="utf-8",
    )

    versions.activate("1.0.64")
    assert versions.active_version() == "1.0.64"
    versions.activate("1.0.63")

    assert versions.active_version() == "1.0.63"
    assert versions.stable_path().read_bytes() == b"launcher"
    assert versions.version_binary("1.0.64").read_text() == "sixtyfour"


def test_windows_before_the_launcher_keeps_a_copy_and_knows_its_version(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine not yet given the launcher still has a copy on PATH.

    Every version assertion here failed on the Windows runners until
    activate recorded the name: a copied file resolves to itself and says
    nothing about where it came from.
    """
    monkeypatch.setattr(versions, "NATIVE_WINDOWS", True)
    monkeypatch.setattr(versions, "is_launcher", lambda path: False)
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


def test_only_the_recorded_launcher_counts(layout: Path) -> None:
    stable = versions.stable_path()
    stable.write_bytes(b"launcher")
    assert not versions.is_launcher(stable)

    (layout / "share").mkdir(exist_ok=True)
    (layout / "share" / "launcher.sha256").write_text(
        hashlib.sha256(b"launcher").hexdigest(), encoding="utf-8",
    )
    assert versions.is_launcher(stable)

    # 舊格局留在 PATH 上的複本對不上記錄
    stable.write_bytes(b"a copy of a onefile build")
    assert not versions.is_launcher(stable)
    assert not versions.is_launcher(layout / "absent.exe")


def test_a_record_pointing_at_a_deleted_version_is_ignored(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(versions, "NATIVE_WINDOWS", True)
    versions.place("1.0.64", _release(layout, "sixtyfour"))
    versions.activate("1.0.64")

    import shutil as shutil_mod
    shutil_mod.rmtree(versions.version_dir("1.0.64"))

    assert versions.active_version() is None


def test_a_version_run_through_the_launcher_is_the_managed_copy(layout: Path) -> None:
    """On Windows PATH holds the launcher; what runs is the version it picked."""
    binary = versions.place("1.0.99", _release(layout, "ninetynine"))

    assert versions.is_managed(binary)
    assert not versions.is_managed(layout / "Desktop" / "acg.exe")


def _onedir(layout: Path, version: str) -> Path:
    app = versions.version_dir(version) / versions.APP_DIR
    (app / "_internal").mkdir(parents=True)
    binary = app / versions.executable_name()
    binary.write_text(version, encoding="utf-8")
    return binary


def test_a_onedir_build_is_found_in_its_app_directory(layout: Path) -> None:
    binary = _onedir(layout, "1.0.99")
    versions.place("1.0.98", _release(layout, "ninetyeight"))

    assert versions.version_binary("1.0.99") == binary
    assert versions.version_binary("1.0.98") == versions.version_dir("1.0.98") / versions.executable_name()
    assert versions.installed_versions() == ["1.0.98", "1.0.99"]


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs a privilege on Windows")
def test_posix_links_the_stable_path_into_app(layout: Path) -> None:
    binary = _onedir(layout, "1.0.99")

    versions.activate("1.0.99")

    assert versions.active_version() == "1.0.99"
    assert versions.stable_path().resolve() == binary.resolve()


def test_windows_launcher_switches_to_a_onedir_build(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(versions, "NATIVE_WINDOWS", True)
    _onedir(layout, "1.0.99")
    versions.stable_path().write_bytes(b"launcher")
    (layout / "share" / "launcher.sha256").write_text(
        hashlib.sha256(b"launcher").hexdigest(), encoding="utf-8",
    )

    versions.activate("1.0.99")

    assert versions.active_version() == "1.0.99"
    assert versions.stable_path().read_bytes() == b"launcher"


def test_a_onedir_exe_is_never_copied_away_from_its_runtime(
    layout: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the launcher on PATH, only the installer can switch to onedir."""
    monkeypatch.setattr(versions, "NATIVE_WINDOWS", True)
    _onedir(layout, "1.0.99")
    versions.stable_path().write_bytes(b"old onefile copy")

    with pytest.raises(versions.NeedsInstaller):
        versions.activate("1.0.99")
    assert versions.stable_path().read_bytes() == b"old onefile copy"
