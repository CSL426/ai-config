"""Unit tests for the GUI bridge layer (ai_config/gui.py).

GuiApi wraps the CLI in-process: it whitelists commands, captures
stdout/stderr, strips ANSI, and pre-feeds stdin for push confirmations.
These tests monkeypatch ai_config.__main__.main so no repo is needed.
"""

import threading
from pathlib import Path

import pytest

import ai_config.__main__ as cli
from ai_config.commands.gui import GuiApi


@pytest.fixture
def api() -> GuiApi:
    return GuiApi()


def test_run_rejects_unknown_command(api: GuiApi) -> None:
    result = api.run("reset")
    assert result["code"] == 1
    assert "not allowed" in result["output"]


def test_run_rejects_unknown_tool(api: GuiApi) -> None:
    result = api.run("status", tool="vim")
    assert result["code"] == 1
    assert "Unknown tool" in result["output"]


def test_run_captures_stdout_and_strips_ansi(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_main(argv):
        print("\033[0;32m✓\033[0m done")
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    result = api.run("status", tool="claude")
    assert result["code"] == 0
    assert result["output"] == "✓ done\n"


def test_run_passes_command_and_tool(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    api.run("apply", tool="codex")
    assert seen["argv"] == ["apply", "codex"]


def test_run_reports_nonzero_exit(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "main", lambda argv: 3)
    assert api.run("pull")["code"] == 3


def test_run_converts_systemexit_and_exception(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exits(argv):
        raise SystemExit(2)

    monkeypatch.setattr(cli, "main", exits)
    assert api.run("status")["code"] == 2

    def explodes(argv):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "main", explodes)
    result = api.run("status")
    assert result["code"] == 1
    assert "boom" in result["output"]


def test_push_requires_preview(
    api: GuiApi,
) -> None:
    result = api.run("push")
    assert result["code"] == 1
    assert "先預覽" in result["output"]


def test_push_preview_then_confirm_uses_matching_review(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_main(argv):
        print("review: one settings file")
        answer = input("Commit and push these changes? [y/N] ")
        print("uploaded" if answer.strip() == "y" else "cancelled")
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    preview = api.preview_push("codex")
    assert preview["code"] == 0
    assert preview["needs_confirmation"] is True
    assert preview["token"]
    assert "review: one settings file" in preview["output"]
    assert "cancelled" not in preview["output"]

    result = api.confirm_push("codex", preview["token"])
    assert result["code"] == 0
    assert "uploaded" in result["output"]


def test_push_confirmation_rejects_changed_review(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = ["first"]

    def fake_main(argv):
        print(f"review: {review[0]}")
        input("Commit and push these changes? [y/N] ")
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    preview = api.preview_push()
    review[0] = "changed"
    result = api.confirm_push("all", preview["token"])

    assert result["code"] == 1
    assert "已有變動" in result["output"]


def test_push_confirmation_rejects_invalid_preview(api: GuiApi) -> None:
    result = api.confirm_push("all", "missing")
    assert result["code"] == 1
    assert "重新預覽" in result["output"]


def test_run_is_serialized_by_lock(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_main(argv):
        entered.set()
        release.wait(timeout=5)
        return 0

    monkeypatch.setattr(cli, "main", slow_main)
    first = threading.Thread(target=api.run, args=("status",))
    first.start()
    assert entered.wait(timeout=5)
    busy = api.run("status")
    release.set()
    first.join(timeout=5)
    assert busy["code"] == 1
    assert "另一個動作" in busy["output"]


def test_package_skills_rejects_bad_input(api: GuiApi) -> None:
    assert api.package_skills("not-a-list")["code"] == 1
    result = api.package_skills([])
    assert result["code"] == 1
    assert result["zips"] == []


def test_package_skills_packages_each_and_reports(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import ai_config.commands.gui as gui_mod
    import ai_config.package as package_mod

    def fake_package(name, out_dir):
        if name == "missing":
            raise package_mod.SkillNotFoundError(name)
        return out_dir / f"{name}.zip"

    monkeypatch.setattr(package_mod, "package_skill", fake_package)
    monkeypatch.setattr(gui_mod, "_package_output_dir", lambda: tmp_path)

    result = api.package_skills(["wiki888", "missing"])
    assert result["code"] == 1
    assert result["zips"] == [str(tmp_path / "wiki888.zip")]
    assert "✓ 已打包" in result["output"]
    assert "✗ 找不到技能:missing" in result["output"]

    ok = api.package_skills(["wiki888"])
    assert ok["code"] == 0
    assert ok["zips"] == [str(tmp_path / "wiki888.zip")]


def test_package_output_dir_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ai_config.commands.gui import _package_output_dir

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert _package_output_dir() == tmp_path
    (tmp_path / "Downloads").mkdir()
    assert _package_output_dir() == tmp_path / "Downloads"


def test_share_skills_runs_share_per_name(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_main(argv):
        calls.append(argv)
        print(f"shared {argv[1]}")
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    result = api.share_skills(["alpha", "beta"])
    assert result["code"] == 0
    assert calls == [["share", "alpha"], ["share", "beta"]]
    assert "shared alpha" in result["output"]
    assert "shared beta" in result["output"]


def test_share_skills_rejects_bad_input(api: GuiApi) -> None:
    assert api.share_skills("nope")["code"] == 1
    assert api.share_skills([])["code"] == 1


def test_list_skills_reports_shared_and_shareable(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_config.commands.share as share_mod
    import ai_config.package as package_mod

    monkeypatch.setattr(package_mod, "available_skills", lambda: ["wiki"])
    monkeypatch.setattr(
        share_mod, "shareable_skill_names", lambda: ["eli5", "wiki"]
    )
    result = api.list_skills()
    assert result["skills"] == [
        {"name": "eli5", "shared": False, "shareable": True},
        {"name": "wiki", "shared": True, "shareable": True},
    ]


def test_check_update_reports_versions(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_config.commands.update as update_mod
    import ai_config.version as version_mod

    monkeypatch.setattr(version_mod, "current_version", lambda: "1.0.25")
    monkeypatch.setattr(update_mod, "_latest_release_version", lambda: "1.0.26")
    result = api.check_update()
    assert result["code"] == 0
    assert result["up_to_date"] is False
    assert result["latest"] == "1.0.26"

    monkeypatch.setattr(update_mod, "_latest_release_version", lambda: "1.0.25")
    assert api.check_update()["up_to_date"] is True

    def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(update_mod, "_latest_release_version", boom)
    failed = api.check_update()
    assert failed["code"] == 1
    assert "offline" in failed["output"]


def test_run_update_invokes_update_command(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert api.run_update()["code"] == 0
    assert seen["argv"] == ["update"]


def test_setup_repo_builds_argv_and_validates(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert api.setup_repo("", "")["code"] == 1
    assert api.setup_repo(123, "")["code"] == 1

    result = api.setup_repo(" git@host:me/cfg.git ", "/tmp/dir")
    assert result["code"] == 0
    assert seen["argv"] == [
        "setup",
        "--data-dir",
        "/tmp/dir",
        "--repo-url",
        "git@host:me/cfg.git",
    ]

    api.setup_repo("git@host:me/cfg.git", "")
    assert seen["argv"][2].endswith(".acg/data")


def test_get_info_reports_version_and_tools(api: GuiApi) -> None:
    info = api.get_info()
    assert info["tools"] == ["claude", "codex", "agy"]
    assert info["version"]
    assert info["repo"]
    assert info["provider"] in {"git", "gdrive"}


def test_config_info_invokes_read_only_command(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        print("configuration overview")
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    result = api.config_info()

    assert result["code"] == 0
    assert result["output"] == "configuration overview\n"
    assert seen["argv"] == ["config"]


def test_setup_gdrive_builds_argv_and_validates(
    api: GuiApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert api.setup_gdrive(123)["code"] == 1

    result = api.setup_gdrive("/tmp/gdrive_dir")
    assert result["code"] == 0
    assert seen["argv"] == [
        "setup",
        "--provider",
        "gdrive",
        "--data-dir",
        "/tmp/gdrive_dir",
    ]
