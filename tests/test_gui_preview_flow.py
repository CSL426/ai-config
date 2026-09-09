"""GUI preview/confirm flows end to end: previews never touch live files,
confirms apply exactly what was shown, and a stale preview is refused."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_apply_projection import write
from test_commands import make_full_repo

from ai_config.safety import is_reparse_point

DRIVER = """
import json, sys
from pathlib import Path
from ai_config.commands.gui import GuiApi
from ai_config.paths import HOME as home
api = GuiApi()
steps = json.loads(sys.argv[1])
out = {}
for name, call in steps:
    if name == "preview_apply":
        result = api.preview_apply(*call)
    elif name == "confirm_apply":
        result = api.confirm_apply(out[call]["token"])
    elif name == "preview_memory":
        result = api.preview_memory(*call)
    elif name == "confirm_memory":
        result = api.confirm_memory(out[call]["token"])
    elif name == "memory_info":
        result = api.memory_info()
    elif name == "touch_live":
        (home / call).write_text("edited outside\\n", encoding="utf-8")
        result = {"code": 0}
    elif name == "read_live":
        result = {"text": (home / call).read_text(encoding="utf-8")}
    out[name] = result
print(json.dumps(out, ensure_ascii=False))
"""


def _drive(repo_dir: Path, home_dir: Path, steps: list) -> dict:
    env = os.environ.copy()
    # Windows 的 Path.home() 看 USERPROFILE,不看 HOME
    env.update(
        HOME=str(home_dir),
        USERPROFILE=str(home_dir),
        AI_CONFIG_REPO=str(repo_dir),
        PYTHONPATH=str(repo_dir),
    )
    result = subprocess.run(
        [sys.executable, "-c", DRIVER, json.dumps(steps)],
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_apply_preview_is_read_only_and_confirm_applies_with_backup(
    tmp_path: Path,
) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    live = home_dir / ".claude/CLAUDE.md"
    write(live, "live rules\n")

    out = _drive(
        repo_dir,
        home_dir,
        [
            ["preview_apply", ["claude", "settings"]],
            ["read_live", ".claude/CLAUDE.md"],
            ["confirm_apply", "preview_apply"],
        ],
    )
    preview = out["preview_apply"]
    assert preview["code"] == 0 and preview["needs_confirmation"] is True, preview
    assert preview["scope"] == {"tool": "claude", "category": "settings"}
    destinations = {
        Path(c["destination"]).name: c["operation"] for c in preview["changes"]
    }
    assert destinations["CLAUDE.md"] == "modify"
    assert destinations["settings.json"] == "create"
    # 預覽階段 live 不能動
    assert out["read_live"]["text"] == "live rules\n"

    confirm = out["confirm_apply"]
    assert confirm["code"] == 0, confirm
    assert confirm["error"] is None and confirm["recovery_required"] is False
    assert Path(confirm["backup_path"]).is_dir()
    assert live.read_text(encoding="utf-8") == "repo instructions\n"


def test_apply_confirm_refuses_when_live_changed_after_preview(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "live rules\n")

    out = _drive(
        repo_dir,
        home_dir,
        [
            ["preview_apply", ["claude", "settings"]],
            ["touch_live", ".claude/CLAUDE.md"],
            ["confirm_apply", "preview_apply"],
        ],
    )
    confirm = out["confirm_apply"]
    assert confirm["code"] == 1
    assert confirm["error"] == "STALE_PREVIEW"
    # 外部修改保留,沒有被套用覆蓋
    assert (home_dir / ".claude/CLAUDE.md").read_text(
        encoding="utf-8"
    ) == "edited outside\n"


def test_memory_enable_preview_then_confirm_installs_entries(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "live rules\n")

    out = _drive(
        repo_dir,
        home_dir,
        [
            ["preview_memory", ["enable"]],
            ["read_live", ".claude/CLAUDE.md"],
            ["confirm_memory", "preview_memory"],
            ["memory_info", None],
        ],
    )
    preview = out["preview_memory"]
    assert preview["code"] == 0 and preview["needs_confirmation"] is True, preview
    assert {c["operation"] for c in preview["changes"]} >= {"link", "modify", "add"}
    assert out["read_live"]["text"] == "live rules\n"
    assert out["confirm_memory"]["code"] == 0, out["confirm_memory"]
    link = home_dir / ".claude/shared-memory"
    # Windows 用 Junction,不是 symlink
    assert link.is_symlink() or is_reparse_point(link)

    info = out["memory_info"]
    assert info["code"] == 0 and info["shared_status"] == "ok"
    assert {e["tool"]: e["status"] for e in info["entries"]} == {
        "claude": "installed",
        "codex": "installed",
        "agy": "installed",
    }
    assert info["actions"]["enable"] == {"allowed": False, "reason": "已一致"}
    assert info["actions"]["disable"]["allowed"] is True
    assert info["actions"]["adopt"]["allowed"] is False


def test_preview_memory_adopt_without_project_is_stale(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    out = _drive(repo_dir, home_dir, [["preview_memory", ["adopt", "bogus"]]])
    assert out["preview_memory"]["error"] == "STALE_PREVIEW"
    assert not (home_dir / ".claude/shared-memory").exists()


def test_extended_prefix_is_stripped_from_link_targets() -> None:
    from ai_config.review import strip_extended_prefix

    assert (
        strip_extended_prefix(r"\\?\C:\Users\me\.gemini\config\skills")
        == r"C:\Users\me\.gemini\config\skills"
    )
    assert strip_extended_prefix(r"\\?\UNC\server\share\dir") == r"\\server\share\dir"
    assert strip_extended_prefix("/home/me/x") == "/home/me/x"


@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation needs privileges on Windows"
)
def test_shadow_copy_creates_links_after_their_targets(tmp_path: Path) -> None:
    from ai_config.applyplan import _copy_tree, _link

    home = tmp_path / "home"
    (home / "config" / "skills").mkdir(parents=True)
    (home / "config" / "skills" / "a.md").write_text("x", encoding="utf-8")
    # 連結名稱排在目標目錄前面
    (home / "cli").mkdir()
    (home / "cli" / "skills").symlink_to(home / "config" / "skills")
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    deferred: list = []
    for root in ("cli", "config"):
        _copy_tree(home / root, shadow / root, home, shadow, deferred)
    assert len(deferred) == 1 and not (shadow / "cli" / "skills").exists()
    for path, record in deferred:
        _link(path, record)
    link = shadow / "cli" / "skills"
    assert link.is_symlink()
    assert link.resolve() == (shadow / "config" / "skills").resolve()
    assert (link / "a.md").read_text(encoding="utf-8") == "x"


def test_first_agy_skills_preview_then_confirm(tmp_path: Path) -> None:
    repo_dir, home_dir = make_full_repo(tmp_path)
    write(
        repo_dir / "claude/skills/sample/SKILL.md",
        "---\nname: sample\ndescription: Sample skill\n---\nSample\n",
    )
    out = _drive(
        repo_dir,
        home_dir,
        [
            ["preview_apply", ["agy", "skills"]],
            ["confirm_apply", "preview_apply"],
        ],
    )
    assert out["preview_apply"]["code"] == 0, out["preview_apply"]
    assert out["confirm_apply"]["code"] == 0, out["confirm_apply"]
    canonical = home_dir / ".gemini/config/skills"
    alias = home_dir / ".gemini/antigravity-cli/skills"
    assert (canonical / "sample/SKILL.md").is_file()
    assert (alias / "sample/SKILL.md").read_bytes() == (
        canonical / "sample/SKILL.md"
    ).read_bytes()


@pytest.mark.parametrize("fail_junction", [False, True])
def test_junction_apply_orders_targets_and_rolls_back_new_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_junction: bool,
) -> None:
    from ai_config import applyplan, links, review

    home = tmp_path / "home"
    home.mkdir()
    alias = home / ".gemini/antigravity-cli/skills"
    target = home / ".gemini/config/skills"
    skill = target / "sample/SKILL.md"
    content = tmp_path / "sample.md"
    content.write_text("sample\n", encoding="utf-8")
    monkeypatch.setattr(applyplan.paths, "SCRIPT_DIR", tmp_path / "data")
    monkeypatch.setattr(applyplan.paths, "BACKUP_BASE", tmp_path / "backups")
    after = {
        str(alias): {"kind": "junction", "target": str(target)},
        str(target): {"kind": "directory"},
        str(skill.parent): {"kind": "directory"},
        str(skill): review.node(content),
    }
    relevant = [alias, target]
    candidate = applyplan.ApplyPlan(
        tmp_path,
        ["agy"],
        "skills",
        {str(p): {"kind": "missing"} for p in relevant},
        after,
        {str(skill): content},
        [{"destination": name} for name in sorted(after)],
        relevant,
        "",
        [],
    )
    candidate.identity = candidate.current_identity()
    calls = []

    def create_junction(source: Path, destination: Path) -> bool:
        calls.append((source, destination))
        assert source == target
        assert destination == alias
        assert skill.read_bytes() == content.read_bytes()
        if fail_junction:
            return False
        # This test covers replay order without requiring Windows privileges.
        # The end-to-end test above uses the platform's actual adapter.
        destination.mkdir()
        return True

    monkeypatch.setattr(links, "_try_create_junction", create_junction)
    if fail_junction:
        with pytest.raises(applyplan.ApplyFailure, match="Cannot create Junction") as error:
            applyplan.execute(candidate)
        assert error.value.recovery_required is False
        assert list(home.iterdir()) == []
        assert (error.value.backup_path / "manifest.json").is_file()
    else:
        applyplan.execute(candidate)
        assert skill.read_bytes() == content.read_bytes()
        assert alias.is_dir()
    assert calls == [(target, alias)]


def test_failed_junction_does_not_create_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_config import applyplan, links

    target = tmp_path / "missing/target"
    monkeypatch.setattr(links, "_try_create_junction", lambda *_: False)
    with pytest.raises(OSError, match="Cannot create Junction"):
        applyplan._link(
            tmp_path / "alias", {"kind": "junction", "target": str(target)},
        )
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def single_apply_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ai_config import applyplan, review

    monkeypatch.setattr(applyplan.paths, "SCRIPT_DIR", tmp_path / "data")
    monkeypatch.setattr(applyplan.paths, "BACKUP_BASE", tmp_path / "backups")

    def make(destination: Path, desired: dict, content: Path | None = None):
        name = str(destination)
        candidate = applyplan.ApplyPlan(
            tmp_path, ["agy"], "skills",
            {name: review.node(destination)}, {name: desired},
            {name: content} if content is not None else {},
            [{"destination": name}], [destination], "", [],
        )
        candidate.identity = candidate.current_identity()
        return candidate

    return make


def test_apply_copy_failure_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, single_apply_plan,
) -> None:
    from ai_config import applyplan, review

    destination = tmp_path / "home/settings.json"
    write(destination, "original\n")
    content = tmp_path / "incoming"
    write(content, "replacement\n")
    candidate = single_apply_plan(destination, review.node(content), content)
    original_copy = applyplan.shutil.copy2

    def fail_copy(source, target):
        if Path(source) == content:
            Path(target).write_text("partial", encoding="utf-8")
            raise OSError("injected copy failure")
        return original_copy(source, target)

    monkeypatch.setattr(applyplan.shutil, "copy2", fail_copy)
    with pytest.raises(applyplan.ApplyFailure, match="injected copy failure") as error:
        applyplan.execute(candidate)
    assert destination.read_text(encoding="utf-8") == "original\n"
    assert list(destination.parent.iterdir()) == [destination]
    assert error.value.recovery_required is False


@pytest.mark.parametrize("original_kind", ["file", "link"])
@pytest.mark.parametrize("recovery", ["restored", "external", "blocked"])
def test_failed_link_write_restores_or_reports_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, single_apply_plan,
    original_kind: str, recovery: str,
) -> None:
    from ai_config import applyplan, review

    destination = tmp_path / "home/entry"
    destination.parent.mkdir()
    old_target, new_target = tmp_path / "old", tmp_path / "new"
    old_target.mkdir()
    new_target.mkdir()
    kind = "junction" if os.name == "nt" else "symlink"
    if original_kind == "link":
        applyplan._link(destination, {"kind": kind, "target": str(old_target)})
    else:
        write(destination, "original\n")
    original = review.node(destination)
    candidate = single_apply_plan(
        destination, {"kind": kind, "target": str(new_target)},
    )
    original_link, original_copy = applyplan._link, applyplan.shutil.copy2

    def fail_link(path, record):
        if record["target"] == str(new_target):
            if recovery == "external":
                write(path, "external writer\n")
            raise OSError("injected link failure")
        if recovery == "blocked":
            raise OSError("injected recovery failure")
        return original_link(path, record)

    def fail_restore_copy(source, target):
        if recovery == "blocked" and Path(source).parent.name.startswith("apply-review-"):
            raise OSError("injected recovery failure")
        return original_copy(source, target)

    monkeypatch.setattr(applyplan, "_link", fail_link)
    monkeypatch.setattr(applyplan.shutil, "copy2", fail_restore_copy)
    with pytest.raises(applyplan.ApplyFailure, match="injected link failure") as error:
        applyplan.execute(candidate)
    if recovery == "restored":
        assert review.node(destination) == original
        assert error.value.recovery_required is False
    elif recovery == "external":
        assert destination.read_text(encoding="utf-8") == "external writer\n"
        assert error.value.recovery_required is True
    else:
        assert review.node(destination) == {"kind": "missing"}
        assert error.value.recovery_required is True
        assert "injected recovery failure" in str(error.value)
    assert (error.value.backup_path / "manifest.json").is_file()
