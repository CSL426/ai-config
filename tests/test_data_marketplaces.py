"""A directory marketplace inside the data repo syncs as a path every machine can use."""

import json
from pathlib import Path

import pytest

from ai_config import data_marketplaces, paths
from ai_config.tools import claude


def _settings(path: str) -> dict:
    return {
        "enabledPlugins": {"acg-console@acg-mods": True},
        "extraKnownMarketplaces": {
            "acg-mods": {"source": {"source": "directory", "path": path}},
            "acg": {"source": {"source": "github", "repo": "CSL426/ai-config"}},
            "elsewhere": {"source": {"source": "directory", "path": "/srv/plugins"}},
        },
    }


def _path(document: dict, name: str = "acg-mods") -> str:
    return document["extraKnownMarketplaces"][name]["source"]["path"]


@pytest.fixture
def machine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"

    def at(repo: Path) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(paths, "SCRIPT_DIR", repo)

    monkeypatch.setattr(paths, "HOME", home)
    home.mkdir()
    return home, at


@pytest.mark.parametrize("written", ["~/ai-config/data/claude/mods", "~\\ai-config\\data/claude/mods"])
def test_gather_stores_a_repo_path_relative_to_the_repo(machine, written: str) -> None:
    home, at = machine
    at(home / "ai-config" / "data")

    stored = data_marketplaces.portable(_settings(written))

    assert _path(stored) == "<acg-data>/claude/mods"
    assert _path(stored, "elsewhere") == "/srv/plugins"
    assert stored["extraKnownMarketplaces"]["acg"] == _settings("")["extraKnownMarketplaces"]["acg"]


def test_absolute_repo_paths_are_stored_relative_too(machine) -> None:
    home, at = machine
    repo = home / "ai-config" / "data"
    at(repo)

    stored = data_marketplaces.portable(_settings((repo / "claude" / "mods").as_posix()))
    assert _path(stored) == "<acg-data>/claude/mods"


def test_apply_writes_this_machines_repo(machine, tmp_path) -> None:
    home, at = machine
    at(home / ".acg-data")
    assert _path(data_marketplaces.local(_settings("<acg-data>/claude/mods"))) == "~/.acg-data/claude/mods"

    outside = tmp_path / "srv" / "acg"
    at(outside)
    assert _path(data_marketplaces.local(_settings("<acg-data>/claude/mods"))) == f"{outside.as_posix()}/claude/mods"


def test_settings_without_directory_marketplaces_are_untouched(machine) -> None:
    home, at = machine
    at(home / "ai-config" / "data")
    document = {"extraKnownMarketplaces": {"acg": {"source": {"source": "github", "repo": "x/y"}}}}

    assert data_marketplaces.portable(document) is document
    assert data_marketplaces.local(document) is document


def test_one_machine_gathers_another_applies_and_status_agrees(machine) -> None:
    home, at = machine
    at(home / "ai-config" / "data")
    gathered = claude.filter_claude_settings(json.dumps(_settings("~/ai-config/data/claude/mods")))
    assert _path(json.loads(gathered)) == "<acg-data>/claude/mods"

    # 另一台的資料庫放在別處
    at(home / ".acg-data")
    applied = json.loads(claude.merge_claude_settings(gathered, json.dumps({"model": "opus"})))
    assert _path(applied) == "~/.acg-data/claude/mods"
    assert applied["model"] == "opus"
    assert claude.shared_claude_settings(json.dumps(applied)) == claude.shared_claude_settings(gathered)


def test_a_fresh_settings_file_gets_this_machines_path(machine, tmp_path) -> None:
    home, at = machine
    at(home / ".acg-data")
    stage, live = tmp_path / "stage", tmp_path / "live"
    stage.mkdir()
    live.mkdir()
    (stage / "settings.json").write_text(json.dumps(_settings("<acg-data>/claude/mods")), encoding="utf-8")

    claude.apply_internal(stage, live, category="settings")

    written = json.loads((live / "settings.json").read_text(encoding="utf-8"))
    assert _path(written) == "~/.acg-data/claude/mods"


def test_a_path_that_climbs_out_of_the_repo_is_not_stored_relative(machine) -> None:
    home, at = machine
    at(home / "ai-config" / "data")

    stored = data_marketplaces.portable(_settings("~/ai-config/data/../plugins"))
    assert _path(stored) == "~/ai-config/data/../plugins"


def test_a_path_an_older_acg_stored_for_another_machine_lands_in_this_repo(machine) -> None:
    home, at = machine
    repo = home / ".acg-data"
    at(repo)
    (repo / "claude" / "mods").mkdir(parents=True)

    applied = data_marketplaces.local(_settings("~/ai-config/data/claude/mods"))
    assert _path(applied) == "~/.acg-data/claude/mods"
    # 本機沒有對應資料夾、或路徑本來就存在的,照舊
    assert _path(applied, "elsewhere") == "/srv/plugins"
    (home / "ai-config" / "data" / "claude" / "mods").mkdir(parents=True)
    assert _path(data_marketplaces.local(_settings("~/ai-config/data/claude/mods"))) == "~/ai-config/data/claude/mods"
