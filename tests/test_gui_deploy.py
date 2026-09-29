"""The desktop deploy panel: previews write nothing, confirms do exactly what was shown."""

from pathlib import Path

import pytest

from ai_config import gui_deploy, locking, paths
from ai_config.commands import deploy
from ai_config.gui_api import GuiApi


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GuiApi:
    data = tmp_path / "data"
    _write(data / "claude/CLAUDE.md", "mine\n")
    _write(data / "claude/skills/acg/SKILL.md", "acg\n")
    _write(data / "claude/shared/both/acg/SKILL.md", "acg shared\n")
    _write(data / "memory/MEMORY.md", "# index\n")
    for module in (paths, deploy, gui_deploy.paths):
        monkeypatch.setattr(module, "SCRIPT_DIR", data)
    monkeypatch.setattr(paths, "CONFIG_ERROR", "", raising=False)
    monkeypatch.setattr(locking, "BACKUP_BASE", tmp_path / "backup")
    return GuiApi()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    _write(root / "AGENTS.md", "# team\n")
    return root


def _choose(api: GuiApi, project: Path) -> str:
    return api._choose_project(project)["project_token"]


def test_info_lists_every_item_with_its_kind(api: GuiApi, project: Path) -> None:
    info = api.deploy_info(_choose(api, project))

    assert info["code"] == 0
    kinds = {item["name"]: item["kind"] for item in info["items"]}
    assert kinds == {"CLAUDE.md": "claude", "skills/acg": "skill", "memory": "memory"}
    assert info["deployed"] == {"files": 0, "plugins": [], "memory": False, "paths": []}


def test_preview_writes_nothing_and_confirm_does_what_it_showed(api: GuiApi, project: Path) -> None:
    token = _choose(api, project)
    before = _snapshot(project)

    preview = api.preview_deploy(token, ["skills/acg", "memory"])

    assert preview["needs_confirmation"] and preview["token"]
    assert _snapshot(project) == before
    destinations = {Path(c["destination"]).relative_to(project).as_posix() for c in preview["changes"]}
    assert destinations == {".claude/skills/acg", ".agents/skills/acg", "AGENTS.md"}

    done = api.confirm_deploy(preview["token"])

    assert done["code"] == 0, done["output"]
    assert (project / ".agents/skills/acg/SKILL.md").read_text(encoding="utf-8") == "acg shared\n"
    assert "acg:project-memory" in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert api.deploy_info(token)["deployed"]["memory"] is True


def test_a_change_after_preview_is_refused(api: GuiApi, project: Path) -> None:
    token = _choose(api, project)
    preview = api.preview_deploy(token, ["skills/acg"])
    _write(project / ".claude/skills/acg/SKILL.md", "team's own\n")

    refused = api.confirm_deploy(preview["token"])

    assert refused["error"] == "STALE_PREVIEW"
    assert not (project / ".agents").exists()


def test_undeploy_puts_the_project_back(api: GuiApi, project: Path) -> None:
    token = _choose(api, project)
    before = _snapshot(project)
    placed = api.preview_deploy(token, ["CLAUDE.md", "skills/acg", "memory"])
    assert api.confirm_deploy(placed["token"])["code"] == 0

    removal = api.preview_undeploy(token)
    assert removal["needs_confirmation"]
    assert api.confirm_undeploy(removal["token"])["code"] == 0

    assert _snapshot(project) == before
    assert api.preview_undeploy(token)["needs_confirmation"] is False


def test_nothing_to_do_needs_no_confirmation(api: GuiApi, project: Path) -> None:
    token = _choose(api, project)
    first = api.preview_deploy(token, ["skills/acg"])
    api.confirm_deploy(first["token"])

    again = api.preview_deploy(token, ["skills/acg"])

    assert again["needs_confirmation"] is False and again["token"] == ""


def test_a_stale_or_foreign_token_is_refused(api: GuiApi, project: Path, tmp_path: Path) -> None:
    token = _choose(api, project)
    other = tmp_path / "other"
    other.mkdir()
    _choose(api, other)

    assert api.preview_deploy(token, ["skills/acg"])["error"] == "STALE_PREVIEW"
    assert api.confirm_deploy("made-up")["error"] == "STALE_PREVIEW"
    assert not (project / ".claude").exists()


def test_unknown_items_are_rejected(api: GuiApi, project: Path) -> None:
    token = _choose(api, project)

    assert api.preview_deploy(token, ["skills/gone"])["error"] == "INVALID_ARGUMENT"
    assert api.preview_deploy(token, [])["error"] == "INVALID_ARGUMENT"
    assert api.preview_deploy(token, "skills/acg")["error"] == "INVALID_ARGUMENT"


def test_one_chosen_project_serves_memory_and_deploy(api: GuiApi, project: Path) -> None:
    # 兩個面板各選一次專案時,兩邊指的可能不是同一個資料夾
    chosen = api._choose_project(project)
    token = chosen["project_token"]

    assert api.deploy_info(token)["root"] == str(project.resolve())
    assert api._project_path(token) == Path(chosen["memory_root"])
