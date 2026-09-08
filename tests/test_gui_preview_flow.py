"""GUI preview/confirm flows end to end: previews never touch live files,
confirms apply exactly what was shown, and a stale preview is refused."""

import json
import os
import subprocess
import sys
from pathlib import Path

from test_apply_projection import write
from test_commands import make_full_repo

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
    assert preview["code"] == 0 and preview["needs_confirmation"] is True
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
    assert preview["code"] == 0 and preview["needs_confirmation"] is True
    assert {c["operation"] for c in preview["changes"]} >= {"link", "modify", "add"}
    assert out["read_live"]["text"] == "live rules\n"
    assert out["confirm_memory"]["code"] == 0, out["confirm_memory"]
    assert (home_dir / ".claude/shared-memory").is_symlink()

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
