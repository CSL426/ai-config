"""What ships: entry points, installers, versions, --help, and the plugin."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from data_repo_helpers import (
    REPO_ROOT,
    create_data_remote,
    run_data_cli,
)
from packaging_test_helpers import (
    project_version,
)

from ai_config.cli import console_main


def test_pyproject_toml_script_entry() -> None:
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.is_file(), "pyproject.toml must exist at repo root"

    with pyproject_path.open("rb") as file:
        data = tomllib.load(file)

    scripts = data.get("project", {}).get("scripts", {})
    assert scripts.get("ai-config") == "ai_config.cli:console_main"
    assert scripts.get("acg") == "ai_config.cli:console_main"


def test_unix_installer_refreshes_command_cache_with_completion() -> None:
    installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'Activate in this shell: hash -r && source \\"$completion_file\\"' in (
        installer
    )


def test_windows_installer_retries_binary_replacement() -> None:
    installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "function Install-Binary" in installer
    assert "Start-Sleep -Milliseconds 200" in installer
    assert installer.count("Install-Binary ") == 2


def test_console_main_usage_entrypoint(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = os.environ.copy()
    environment.pop("AI_CONFIG_ENTRYPOINT", None)
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(sys, "argv", [sys.argv[0]])

    assert console_main() == 0

    captured = capsys.readouterr()
    assert "ai-config <command> [tool]" in captured.out
    assert "setup" in captured.out


def test_skill_guide_rejects_extra_arguments(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(tmp_path / "missing-data-repo")
    env["AI_CONFIG_ENTRYPOINT"] = "ai-config"
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", "skill", "extra"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1


@pytest.mark.parametrize("command", ["version", "--version", "-V"])
@pytest.mark.parametrize(
    ("executable", "display_name"),
    [
        ("ai-config.exe", "ai-config (acg)"),
        ("acg", "ai-config (acg)"),
        ("acg.exe", "ai-config (acg)"),
    ],
)
def test_console_main_version_uses_shared_product_name(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    executable: str,
    display_name: str,
) -> None:
    environment = os.environ.copy()
    environment.pop("AI_CONFIG_ENTRYPOINT", None)
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(sys, "argv", [executable, command])

    assert console_main() == 0
    assert capsys.readouterr().out.strip() == f"{display_name} {project_version()}"


def test_lowercase_v_is_not_a_version_alias(tmp_path: Path) -> None:
    _, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "-v")

    assert result.returncode == 1
    assert "Unknown command: -v" in result.stderr


def test_reset_is_not_forceable(tmp_path: Path) -> None:
    # reset 會刪光設定檔,--force 不通過這一關
    _remote, data_repo = create_data_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = run_data_cli(data_repo, home, "reset", "--force", input_text="")

    assert "Cancelled" in result.stdout


def test_the_plugin_version_matches_the_project() -> None:
    """外掛的版號是手寫的,發版時很容易忘記跟上。

    忘了的話使用者在 `claude plugin list` 看到的是舊版號,會以為自己沒更新。
    """
    import json
    import tomllib

    project = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    plugin = json.loads(
        (REPO_ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]

    assert plugin == project


def test_plugin_commands_never_run_a_placeholder() -> None:
    """`!` 開頭的行會被 Claude Code 直接執行,佔位符會原封不動送進去。

    實際發生過:`!`acg memory handoff write "<名稱>" "<內容>"`` 寫出了一則
    名字就叫「名稱」的交接。要嘛用 $ARGUMENTS,要嘛讓模型自己組指令。
    """
    offenders = []
    for path in sorted((REPO_ROOT / "plugin").rglob("*.md")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("!") and ("<" in line and ">" in line):
                offenders.append(f"{path.name}:{number}")

    assert offenders == []


def test_every_acg_subcommand_has_its_reference_and_no_reference_is_orphaned() -> None:
    """The skill reads a subcommand's steps from its reference file on demand.

    A row pointing at a missing file leaves the model with nothing to follow;
    a file no row points at is never read and quietly goes stale.
    """
    import re

    skill = REPO_ROOT / "plugin/skills/acg"
    listed = set(re.findall(r"`references/([a-z-]+\.md)`", (skill / "SKILL.md").read_text(encoding="utf-8")))
    present = {path.name for path in (skill / "references").glob("*.md")}

    assert listed, "子指令表沒有列出任何參考檔"
    assert listed == present


def test_handoff_reminder_management_is_documented_on_agent_surfaces() -> None:
    from ai_config.guide import render_guide

    surfaces = {
        "guide": render_guide(),
        "README": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "plugin": (REPO_ROOT / "plugin/skills/acg/references/handoff.md").read_text(
            encoding="utf-8"
        ),
    }
    for name, content in surfaces.items():
        for action in ("status", "enable", "disable"):
            assert f"memory handoff remind {action}" in content, (name, action)
        assert "PreCompact" in content, name

    skill = (REPO_ROOT / "plugin/skills/acg/SKILL.md").read_text(encoding="utf-8")
    frontmatter = skill.split("---", 2)[1]
    assert "Bash(acg memory handoff:*)" in frontmatter
    assert "Bash(ai-config memory handoff:*)" in frontmatter


def test_plugin_content_changes_carry_a_version_bump() -> None:
    """An edit under plugin/ that keeps the version cannot reach anyone.

    `claude plugin update` compares versions, not content: same number,
    same install, no matter what changed inside. The command files sat
    eleven releases behind precisely because nothing checked this.
    """
    import json
    import subprocess

    tag = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout.strip()
    if not tag:
        pytest.skip("no tag to compare against")

    # 比到工作區,不是只比到 HEAD:忘記升版號通常在還沒提交時就看得出來
    changed = subprocess.run(
        ["git", "diff", "--name-only", tag, "--", "plugin/"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout.strip()
    if not changed:
        return

    released = subprocess.run(
        ["git", "show", f"{tag}:plugin/.claude-plugin/plugin.json"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout
    if not released:
        return
    current = json.loads(
        (REPO_ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]
    assert current != json.loads(released)["version"], (
        f"plugin/ 改了但版號還是 {current};{tag} 之後改的檔案:\n{changed}"
    )


def test_attribution_stays_disabled_in_the_database() -> None:
    """The rule file once claimed this was set when the key did not exist.

    Only the setting stops the trailers; a sentence in a rules file does
    not, which is how one reached a commit. A model agreeing to leave the
    session link out holds until that conversation ends — the next one is
    told to add it again, so nothing short of the setting settles it.

    includeCoAuthoredBy is deprecated and never covered the session link
    at all; attribution replaces it and covers all three.

    sessionUrl: true is the switch turned ON. This test once asserted it,
    guarding the wrong value: every new session was told to end commits
    with a Claude-Session line, and one reached a pushed commit.
    """
    import json

    database = REPO_ROOT / "data/claude/settings.json"
    if not database.is_file():
        pytest.skip("no data repository checked out here")
    settings = json.loads(database.read_text(encoding="utf-8-sig"))
    attribution = settings.get("attribution")

    assert isinstance(attribution, dict), "attribution 不見了,三台都會重新開始加"
    assert attribution.get("sessionUrl") is False, "sessionUrl: true 會叫每個 session 加 Claude-Session"
    assert attribution.get("commit") == ""
    assert attribution.get("pr") == ""
    assert "includeCoAuthoredBy" not in settings, "已棄用,由 attribution 取代"


def test_both_installers_lay_out_versions_the_same_way() -> None:
    """The layout has to exist on every platform, or update means overwrite.

    install.sh grew version directories; install.ps1 did not, and nothing
    compared them. Two Windows updates ran without ever creating one, and
    the running exe was overwritten in place each time — which is exactly
    what the layout exists to avoid, on the one platform that cannot do it.
    """
    posix = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    windows = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")

    for token in ("versions", "active"):
        assert token in posix, f"install.sh lost its {token} handling"
        assert token in windows, f"install.ps1 never learned about {token}"


def test_adoption_retries_and_says_when_it_gives_up() -> None:
    """The old exe is asked for its version the instant it stops running.

    A Windows machine updated from 1.0.67 into the new layout and the
    1.0.67 binary was never adopted: the installer asked it for a version
    while the file was still locked, got nothing, and returned silently.
    The one machine the adoption path exists for is the one where it is
    most likely to be asked too early.
    """
    installer = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    adopt = installer.split("function Adopt-ExistingBinary")[1].split("\nfunction ")[0]

    assert "Wait-ExecutableReady" in adopt, (
        "adoption reads the version without waiting for the file to be usable"
    )
    assert "Write-Warn" in adopt, "giving up on adoption must not be silent"


def test_the_database_rules_match_the_source_block() -> None:
    """A gather run before a source edit writes the old wording.

    That happened: the rules block gained six handoff headings, the
    gather had already run, and the commit message described the new
    format while the file carried the old one. Every machine then pulled
    a CLAUDE.md that disagreed with the code that generates it, and
    nothing said so.
    """
    from ai_config import memory_paths

    database = REPO_ROOT / "data/claude/CLAUDE.md"
    if not database.is_file():
        pytest.skip("no data repository checked out here")
    stored = database.read_text(encoding="utf-8-sig")
    if memory_paths.BLOCK_BEGIN not in stored:
        pytest.skip("shared memory not enabled in this database")

    begin = stored.index(memory_paths.BLOCK_BEGIN)
    end = stored.index(memory_paths.BLOCK_END) + len(memory_paths.BLOCK_END)

    assert stored[begin:end] == memory_paths.RULES_BLOCK.strip(), (
        "資料庫的規則區塊跟原始碼不一致;改完 RULES_BLOCK 要重跑 acg init claude"
    )


def test_plugin_skills_are_named_and_do_not_shadow_a_command() -> None:
    """A skill fires on what the user says; it needs a description to fire on.

    People say "交接" or "接著做" more often than they type the slash
    command. A skill named like a command would collide with it under the
    plugin's prefix, so one of the two would silently win.
    """
    commands = {path.stem for path in (REPO_ROOT / "plugin/commands").glob("*.md")}
    skills = sorted((REPO_ROOT / "plugin/skills").glob("*/SKILL.md"))
    assert skills, "plugin 沒有任何 skill"
    for path in skills:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), path
        front = text.split("---", 2)[1]
        name = next(
            (line.split(":", 1)[1].strip() for line in front.splitlines()
             if line.startswith("name:")), "",
        )
        assert name == path.parent.name, path
        assert "description:" in front, path
        assert name not in commands, f"{name} 跟指令撞名"


@pytest.mark.parametrize(
    ("command", "usage"),
    [
        (["memory", "--help"], "memory <status"),
        (["memory", "handoff", "--help"], "memory handoff [list"),
        (["memory", "handoff", "remind", "-h"], "memory handoff remind [status"),
        (["memory", "autopush", "--help"], "memory autopush [status"),
        (["msg", "--help"], "msg list | setup"),
        (["update", "--help"], "update [version]"),
    ],
)
def test_subcommand_help_prints_its_usage_and_succeeds(
    tmp_path: Path, command: list[str], usage: str,
) -> None:
    # update --help 以前會被當成版本號,真的去下載一個叫 --help 的版本
    env = os.environ.copy()
    env["AI_CONFIG_REPO"] = str(tmp_path / "missing-data-repo")
    env["AI_CONFIG_ENTRYPOINT"] = "acg"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "ai_config", *command],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False,
        timeout=60,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"Usage: acg {usage}" in output
    assert "✗" not in output
