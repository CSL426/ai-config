"""Native chooser and CLI bridge contracts, isolated from live configuration."""

import sys
from types import SimpleNamespace

import pytest

import ai_config.__main__ as cli
from ai_config import paths
from ai_config.commands import skill
from ai_config.commands.gui import GuiApi


@pytest.fixture
def api(monkeypatch, tmp_path):
    repository = tmp_path / "repository"
    (repository / "claude").mkdir(parents=True)
    monkeypatch.setattr(paths, "SCRIPT_DIR", repository)
    monkeypatch.setattr(paths, "CONFIG_ERROR", None)
    return GuiApi()


def chooser(monkeypatch, selected):
    calls = []

    def select(kind):
        calls.append(kind)
        if isinstance(selected, Exception):
            raise selected
        return selected

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        FileDialog=SimpleNamespace(FOLDER="folder"),
        windows=[SimpleNamespace(create_file_dialog=select)],
    ))
    return calls


def test_native_folder_selection_returns_path_without_install(
    api, monkeypatch, tmp_path,
):
    source = tmp_path / "local skill"
    source.mkdir()
    calls = chooser(monkeypatch, [str(source)])
    result = api.select_skill_directory()
    assert result["code"] == 0
    assert result["cancelled"] is False
    assert result["path"] == str(source)
    assert calls == ["folder"]
    assert list((paths.SCRIPT_DIR / "claude").iterdir()) == []
    assert not api._lock.locked()


@pytest.mark.parametrize("selected", [None, ()])
def test_cancel_does_not_return_a_source(api, monkeypatch, selected):
    chooser(monkeypatch, selected)
    result = api.select_skill_directory()
    assert result["code"] == 0
    assert result["cancelled"] is True
    assert result["path"] is None
    assert not api._lock.locked()


def test_chooser_error_releases_lock(api, monkeypatch):
    chooser(monkeypatch, OSError("native chooser failed"))
    result = api.select_skill_directory()
    assert result["code"] == 1
    assert result["path"] is None
    assert "native chooser failed" in result["output"]
    assert not api._lock.locked()


def test_chooser_requires_configuration(api, monkeypatch):
    calls = chooser(monkeypatch, None)
    monkeypatch.setattr(paths, "CONFIG_ERROR", "configuration unavailable")
    assert api.select_skill_directory()["code"] == 1
    assert calls == []


def test_chooser_busy_does_not_open_dialog(api, monkeypatch):
    calls = chooser(monkeypatch, None)
    with api._lock:
        result = api.select_skill_directory()
        assert result["error"] == "BUSY"
        assert api._lock.locked()
    assert calls == []


def test_chooser_preserves_symlink_for_cli_safety_check(
    api, monkeypatch, tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    link = tmp_path / "linked-source"
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable on this platform")
    chooser(monkeypatch, [str(link)])
    result = api.select_skill_directory()
    assert result["code"] == 0
    assert result["path"] == str(link)


def test_chooser_rejects_file(api, monkeypatch, tmp_path):
    source = tmp_path / "SKILL.md"
    source.write_text("not a directory", encoding="utf-8")
    chooser(monkeypatch, [str(source)])
    assert api.select_skill_directory()["code"] == 1


def test_add_dispatches_fixed_command_and_captures_output(
    api, monkeypatch, tmp_path,
):
    source = str(tmp_path / "local skill & example")
    calls = []

    def main(argv):
        calls.append(argv)
        print("\033[32mInstalled example\033[0m")
        return 0

    monkeypatch.setattr(cli, "main", main)
    assert api.add_skill(source) == {
        "code": 0, "output": "Installed example\n",
    }
    assert calls == [["skill", "add", source]]
    assert not api._lock.locked()


@pytest.mark.parametrize("source", [None, [], {}, 1, "", "  ", "--force",
                                   "-f", "relative/path", "/bad\0path"])
def test_invalid_source_cannot_dispatch(api, monkeypatch, source):
    monkeypatch.setattr(cli, "main", lambda argv: pytest.fail(str(argv)))
    assert api.add_skill(source)["error"] == "INVALID_ARGUMENT"


def test_add_busy_cannot_dispatch(api, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "main", lambda argv: pytest.fail(str(argv)))
    with api._lock:
        assert api.add_skill(str(tmp_path))["error"] == "BUSY"
        assert api._lock.locked()


def test_add_requires_configuration(api, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "main", lambda argv: pytest.fail(str(argv)))
    monkeypatch.setattr(paths, "CONFIG_ERROR", "configuration unavailable")
    assert api.add_skill(str(tmp_path))["code"] == 1
    assert not api._lock.locked()


@pytest.mark.parametrize("command", ["skill", "skill remove", "skill_remove"])
def test_generic_run_still_rejects_skill_commands(api, monkeypatch, command):
    monkeypatch.setattr(cli, "main", lambda argv: pytest.fail(str(argv)))
    assert api.run(command)["code"] == 1


def test_add_uses_cli_install_and_refuses_duplicate(
    api, monkeypatch, tmp_path,
):
    home = tmp_path / "claude-home"
    source = tmp_path / "local source"
    source.mkdir()
    content = "---\nname: example\ndescription: Test skill\n---\nInstructions\n"
    (source / "SKILL.md").write_text(content, encoding="utf-8")
    (source / "auth.json").write_text("excluded", encoding="utf-8")
    (source / ".ai-config-test").write_text("excluded", encoding="utf-8")
    monkeypatch.setattr(skill, "SCRIPT_DIR", paths.SCRIPT_DIR)
    monkeypatch.setattr(skill, "CLAUDE_HOME", home)
    monkeypatch.setattr(cli, "SCRIPT_DIR", paths.SCRIPT_DIR)
    monkeypatch.setattr(cli, "CONFIG_ERROR", None)
    result = api.add_skill(str(source))
    assert result["code"] == 0
    destinations = [paths.SCRIPT_DIR / "claude/skills/example",
                    home / "skills/example"]
    for destination in destinations:
        assert (destination / "SKILL.md").read_text(encoding="utf-8") == content
        assert sorted(p.name for p in destination.iterdir()) == ["SKILL.md"]
    assert api.add_skill(str(source))["code"] == 1
    for destination in destinations:
        assert (destination / "SKILL.md").read_text(encoding="utf-8") == content
