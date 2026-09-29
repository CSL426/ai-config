"""Tests for `deploy`: what is global elsewhere, installed into one project only."""

from pathlib import Path

from test_apply_projection import run_ai_config, write
from test_sync_logic import make_repo


def make_populated_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir, home_dir = make_repo(tmp_path)
    write(repo_dir / "claude/CLAUDE.md", "instructions\n")
    write(repo_dir / "claude/settings.json", '{"theme": "dark"}\n')
    write(repo_dir / "claude/rules/common/style.md", "rules\n")
    write(repo_dir / "claude/commands/commit.md", "---\ndescription: x\n---\n")
    write(repo_dir / "claude/skills/acg/SKILL.md", "---\nname: acg\n---\nbody\n")
    return repo_dir, home_dir


def test_deploy_all_copies_every_managed_item(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (project / ".claude/CLAUDE.md").read_text(encoding="utf-8") == "instructions\n"
    # 個人的整份 settings.json 會蓋掉專案原有設定,不部署
    assert not (project / ".claude/settings.json").exists()
    assert (project / ".claude/rules/common/style.md").is_file()
    assert (project / ".claude/commands/commit.md").is_file()
    assert (project / ".claude/skills/acg/SKILL.md").is_file()


def test_deploy_selection_copies_only_chosen_items(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    # Menu: 1 CLAUDE.md, 2 rules, 3 commands, 4 skills/acg.
    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="1\ny\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (project / ".claude/CLAUDE.md").is_file()
    assert not (project / ".claude/skills").exists()
    assert not (project / ".claude/rules").exists()


def test_deploy_cancelled_writes_nothing(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="a\nn\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert not (project / ".claude").exists()


def test_deploy_rejects_missing_directory(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(tmp_path / "nope"), input_text="a\ny\n"
    )

    assert result.returncode == 1
    assert "not a directory" in (result.stderr + result.stdout).lower()


def test_deploy_defaults_to_current_directory(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)

    # No path argument: the repo dir itself is the cwd for run_ai_config.
    result = run_ai_config(repo_dir, home_dir, "deploy", input_text="1\ny\n")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (repo_dir / ".claude/CLAUDE.md").is_file()


def make_multi_skill_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    write(repo_dir / "claude/skills/wiki888/SKILL.md", "---\nname: wiki888\n---\nb\n")
    write(repo_dir / "claude/skills/hallmark/SKILL.md", "---\nname: hallmark\n---\nb\n")
    return repo_dir, home_dir


def test_deploy_menu_lists_skills_individually(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)

    result = run_ai_config(repo_dir, home_dir, "deploy", str(tmp_path), input_text="\n")

    assert "skills/acg" in result.stdout
    assert "skills/hallmark" in result.stdout
    assert "skills/wiki888" in result.stdout


def test_deploy_selects_a_single_skill(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    # Menu: 1 CLAUDE.md, 2 rules, 3 commands, then skills: 4 acg, 5 hallmark, 6 wiki888.
    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), input_text="5\ny\n"
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (project / ".claude/skills/hallmark/SKILL.md").is_file()
    assert not (project / ".claude/skills/acg").exists()
    assert not (project / ".claude/skills/wiki888").exists()


def test_deploy_save_as_then_profile_replays_selection(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)
    first = tmp_path / "first"
    first.mkdir()

    saved = run_ai_config(
        repo_dir,
        home_dir,
        "deploy",
        str(first),
        "--save-as",
        "frontend",
        input_text="1 5\ny\n",
    )
    assert saved.returncode == 0, saved.stderr + saved.stdout
    assert (repo_dir / "claude/deploy-profiles.toml").is_file()

    second = tmp_path / "second"
    second.mkdir()
    replayed = run_ai_config(
        repo_dir, home_dir, "deploy", str(second), "--profile", "frontend"
    )

    assert replayed.returncode == 0, replayed.stderr + replayed.stdout
    assert (second / ".claude/CLAUDE.md").is_file()
    assert (second / ".claude/skills/hallmark/SKILL.md").is_file()
    assert not (second / ".claude/skills/acg").exists()


def test_deploy_unknown_profile_fails(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)

    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(tmp_path), "--profile", "nope"
    )

    assert result.returncode == 1
    assert "unknown profile" in (result.stdout + result.stderr).lower()


def test_deploy_profile_reports_items_removed_from_repo(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    write(
        repo_dir / "claude/deploy-profiles.toml",
        '[stale]\nitems = ["skills/gone"]\n',
    )
    result = run_ai_config(
        repo_dir, home_dir, "deploy", str(project), "--profile", "stale"
    )

    assert result.returncode == 1
    assert "skills/gone" in result.stdout + result.stderr
    assert not (project / ".claude").exists()


def test_deploy_rejects_profile_combined_with_save_as(tmp_path: Path) -> None:
    repo_dir, home_dir = make_multi_skill_repo(tmp_path)

    result = run_ai_config(
        repo_dir,
        home_dir,
        "deploy",
        str(tmp_path),
        "--profile",
        "a",
        "--save-as",
        "b",
    )

    assert result.returncode == 1
    assert "cannot be combined" in (result.stdout + result.stderr).lower()


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_each_skill_lands_where_its_tools_read_it(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    write(repo_dir / "claude/shared/both/acg/SKILL.md", "---\nname: acg\n---\nshared\n")
    write(repo_dir / "claude/shared/codex/only-codex/SKILL.md", "x\n")
    write(repo_dir / "claude/shared/agy/only-agy/SKILL.md", "y\n")
    project = tmp_path / "proj"
    project.mkdir()

    result = run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")

    assert result.returncode == 0, result.stderr + result.stdout
    # 同名技能是選單上的一項,放到它全域時會去的每個位置
    assert result.stdout.count("skills/acg  (") == 1
    assert "skills/acg  (Claude, Codex, agy)" in result.stdout
    assert (project / ".claude/skills/acg/SKILL.md").read_text(encoding="utf-8").endswith("body\n")
    assert (project / ".agents/skills/acg/SKILL.md").read_text(encoding="utf-8").endswith("shared\n")
    assert (project / ".codex/skills/only-codex/SKILL.md").is_file()
    assert (project / ".agent/skills/only-agy/SKILL.md").is_file()
    assert not (project / ".agents/skills/only-codex").exists()


def test_the_projects_own_files_are_never_deleted_or_overwritten(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    write(repo_dir / "claude/agents/reviewer.md", "mine\n")
    project = tmp_path / "proj"
    write(project / ".claude/agents/team-reviewer.md", "team\n")
    write(project / ".claude/settings.json", '{"permissions": {"allow": ["Bash(npm test)"]}}\n')
    write(project / ".claude/CLAUDE.md", "team rules\n")
    write(project / ".claude/skills/acg/SKILL.md", "team's acg\n")
    before = _snapshot(project)

    result = run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")

    after = _snapshot(project)
    assert all(after.get(path) == content for path, content in before.items())
    assert (project / ".claude/agents/reviewer.md").is_file()
    output = result.stdout + result.stderr
    assert ".claude/CLAUDE.md" in output and ".claude/skills/acg" in output
    # 有東西沒放成就不能說成功
    assert result.returncode == 1


def test_running_it_again_changes_nothing_and_succeeds(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    first = run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")
    assert first.returncode == 0, first.stderr + first.stdout
    placed = _snapshot(project)

    again = run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")

    assert again.returncode == 0, again.stderr + again.stdout
    assert _snapshot(project) == placed


def test_deploy_writes_nothing_under_home(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    write(home_dir / ".claude/CLAUDE.md", "host owner\n")
    write(home_dir / ".claude/skills/host-skill/SKILL.md", "host\n")
    project = tmp_path / "proj"
    project.mkdir()
    before = _snapshot(home_dir)

    result = run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")

    assert result.returncode == 0, result.stderr + result.stdout
    assert _snapshot(home_dir) == before


def _plugin_repo(tmp_path: Path, monkeypatch) -> tuple[Path, list]:
    import json

    from ai_config.commands import deploy

    source = tmp_path / "data/claude"
    write(source / "settings.json", json.dumps({
        "enabledPlugins": {"eli5@community": True, "extra@official": False},
        "extraKnownMarketplaces": {
            "community": {"source": {"source": "github", "repo": "owner/community"}},
        },
    }))
    write(source / "deploy-profiles.toml", '[p]\nitems = ["plugins/eli5@community"]\n')
    monkeypatch.setattr(deploy, "SCRIPT_DIR", tmp_path / "data")
    calls: list = []
    return source, calls


def test_a_plugin_is_enabled_for_the_project_only(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    from ai_config.commands import deploy

    _, calls = _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()

    def fake(args, cwd):
        calls.append((args, cwd))
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(deploy, "_run_claude", fake)
    assert deploy.run_deploy(str(project), profile="p") == 0
    assert calls == [(["install", "eli5@community", "--scope", "project"], project.resolve())]


def test_an_unknown_marketplace_is_declared_in_the_project(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    from ai_config.commands import deploy

    _, calls = _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    known: set = set()

    def fake(args, cwd):
        calls.append(args)
        if args[0] == "marketplace":
            known.add("community")
            return subprocess.CompletedProcess(args, 0, "", "")
        code = 0 if "community" in known else 1
        return subprocess.CompletedProcess(args, code, "", "marketplace not found")

    monkeypatch.setattr(deploy, "_run_claude", fake)
    assert deploy.run_deploy(str(project), profile="p") == 0
    # 宣告在專案範圍,不加進主機的 marketplace 清單
    assert ["marketplace", "add", "owner/community", "--scope", "project"] in calls


def test_a_failed_plugin_install_is_reported_not_hidden(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    from ai_config.commands import deploy

    _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr(
        deploy, "_run_claude",
        lambda args, cwd: subprocess.CompletedProcess(args, 1, "", "network down"),
    )
    assert deploy.run_deploy(str(project), profile="p") == 1


def test_an_already_enabled_plugin_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    from ai_config.commands import deploy

    _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    write(project / ".claude/settings.json", '{"enabledPlugins": {"eli5@community": true}}')

    def refuse(args, cwd):
        raise AssertionError("should not call claude")

    monkeypatch.setattr(deploy, "_run_claude", refuse)
    assert deploy.run_deploy(str(project), profile="p") == 0


def _with_memory(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    write(repo_dir / "memory/MEMORY.md", "# index\n")
    return repo_dir, home_dir


def test_memory_rules_go_into_agents_md_pointing_at_this_machine(tmp_path: Path) -> None:
    repo_dir, home_dir = _with_memory(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    result = run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")

    assert result.returncode == 0, result.stderr + result.stdout
    rules = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "acg:project-memory:begin" in rules
    # 別人的主機上沒有全域連結;規則要直接指到這台的資料庫
    assert "shared-memory" not in rules
    assert f"{(repo_dir / 'memory').resolve().as_posix()}/MEMORY.md" in rules
    assert not (home_dir / ".claude/shared-memory").exists()


def test_remove_restores_the_project_exactly(tmp_path: Path) -> None:
    repo_dir, home_dir = _with_memory(tmp_path)
    write(repo_dir / "claude/shared/both/acg/SKILL.md", "shared\n")
    project = tmp_path / "proj"
    write(project / "AGENTS.md", "# team rules\n")
    write(project / ".claude/rules/team.md", "team\n")
    before = _snapshot(project)

    placed = run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")
    assert placed.returncode == 0, placed.stderr + placed.stdout
    assert (project / ".acg-deploy.json").is_file()
    assert (project / ".agents/skills/acg/SKILL.md").is_file()

    removed = run_ai_config(repo_dir, home_dir, "deploy", str(project), "--remove", input_text="y\n")

    assert removed.returncode == 0, removed.stderr + removed.stdout
    assert _snapshot(project) == before
    assert not (project / ".agents").exists()
    assert (project / ".claude/rules").is_dir()


def test_remove_deletes_an_agents_md_it_created(tmp_path: Path) -> None:
    repo_dir, home_dir = _with_memory(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")
    assert (project / "AGENTS.md").is_file()

    removed = run_ai_config(repo_dir, home_dir, "deploy", str(project), "--remove", input_text="y\n")

    assert removed.returncode == 0, removed.stderr + removed.stdout
    assert list(project.iterdir()) == []


def test_remove_keeps_a_file_edited_after_deploy(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    run_ai_config(repo_dir, home_dir, "deploy", str(project), input_text="a\ny\n")
    edited = project / ".claude/skills/acg/SKILL.md"
    edited.write_text("my own notes\n", encoding="utf-8")

    removed = run_ai_config(repo_dir, home_dir, "deploy", str(project), "--remove", input_text="y\n")

    assert removed.returncode == 1
    assert edited.read_text(encoding="utf-8") == "my own notes\n"
    assert not (project / ".claude/CLAUDE.md").exists()
    assert ".claude/skills/acg/SKILL.md" in (project / ".acg-deploy.json").read_text(encoding="utf-8")


def test_remove_ignores_record_entries_outside_the_project(tmp_path: Path) -> None:
    import json

    repo_dir, home_dir = make_populated_repo(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    from ai_config.deploy_record import digest

    (project / ".acg-deploy.json").write_text(json.dumps({"files": {
        "../victim.txt": digest(victim), str(victim): digest(victim),
    }}), encoding="utf-8")

    result = run_ai_config(repo_dir, home_dir, "deploy", str(project), "--remove", input_text="y\n")

    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert "不在專案裡" in result.stdout + result.stderr


def test_remove_without_a_record_says_so(tmp_path: Path) -> None:
    repo_dir, home_dir = make_populated_repo(tmp_path)

    result = run_ai_config(repo_dir, home_dir, "deploy", str(tmp_path), "--remove")

    assert result.returncode == 0
    assert "沒有 acg 部署紀錄" in result.stdout + result.stderr


def test_remove_takes_plugins_and_marketplaces_off_the_project(
    tmp_path: Path, monkeypatch,
) -> None:
    import subprocess

    from ai_config.commands import deploy

    _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    calls: list = []
    known: set = set()

    def fake(args, cwd):
        calls.append(args)
        if args[:2] == ["marketplace", "add"]:
            known.add("community")
        ok = args[0] != "install" or "community" in known
        return subprocess.CompletedProcess(args, 0 if ok else 1, "", "")

    monkeypatch.setattr(deploy, "_run_claude", fake)
    monkeypatch.setattr(deploy, "confirm_prompt", lambda *_a, **_k: True)
    assert deploy.run_deploy(str(project), profile="p") == 0
    assert deploy.run_undeploy(str(project)) == 0
    assert ["uninstall", "eli5@community", "--scope", "project"] in calls
    assert ["marketplace", "remove", "community", "--scope", "project"] in calls
    assert not (project / ".acg-deploy.json").exists()


def test_a_partial_deploy_still_records_what_it_placed(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    from ai_config.commands import deploy

    source, _ = _plugin_repo(tmp_path, monkeypatch)
    write(source / "skills/acg/SKILL.md", "x\n")
    write(source / "deploy-profiles.toml", '[p]\nitems = ["skills/acg", "plugins/eli5@community"]\n')
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr(
        deploy, "_run_claude",
        lambda args, cwd: subprocess.CompletedProcess(args, 1, "", "offline"),
    )

    assert deploy.run_deploy(str(project), profile="p") == 1
    assert ".claude/skills/acg/SKILL.md" in (project / ".acg-deploy.json").read_text(encoding="utf-8")


def _claude_that_rewrites_settings(project: Path):
    """Mimics Claude: install adds the key, uninstall leaves `enabledPlugins: {}` reformatted."""
    import json
    import subprocess

    path = project / ".claude/settings.json"

    def fake(args, cwd):
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if args[0] == "install":
            current.setdefault("enabledPlugins", {})[args[1]] = True
        elif args[0] == "uninstall":
            current.setdefault("enabledPlugins", {}).pop(args[1], None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    return fake


def test_remove_puts_the_projects_settings_back_byte_for_byte(tmp_path: Path, monkeypatch) -> None:
    from ai_config.commands import deploy

    _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    original = '{"permissions":{"allow":["Bash(npm test)"]}}'
    write(project / ".claude/settings.json", original)
    monkeypatch.setattr(deploy, "_run_claude", _claude_that_rewrites_settings(project))
    monkeypatch.setattr(deploy, "confirm_prompt", lambda *_a, **_k: True)

    assert deploy.run_deploy(str(project), profile="p") == 0
    assert deploy.run_undeploy(str(project)) == 0
    assert (project / ".claude/settings.json").read_text(encoding="utf-8") == original


def test_remove_deletes_a_settings_file_only_plugins_created(tmp_path: Path, monkeypatch) -> None:
    from ai_config.commands import deploy

    _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr(deploy, "_run_claude", _claude_that_rewrites_settings(project))
    monkeypatch.setattr(deploy, "confirm_prompt", lambda *_a, **_k: True)

    assert deploy.run_deploy(str(project), profile="p") == 0
    assert deploy.run_undeploy(str(project)) == 0
    assert list(project.iterdir()) == []


def test_later_edits_to_settings_survive_remove(tmp_path: Path, monkeypatch) -> None:
    import json

    from ai_config.commands import deploy

    _plugin_repo(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    write(project / ".claude/settings.json", '{"a": 1}')
    monkeypatch.setattr(deploy, "_run_claude", _claude_that_rewrites_settings(project))
    monkeypatch.setattr(deploy, "confirm_prompt", lambda *_a, **_k: True)
    assert deploy.run_deploy(str(project), profile="p") == 0
    path = project / ".claude/settings.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["b"] = 2
    path.write_text(json.dumps(edited), encoding="utf-8")

    assert deploy.run_undeploy(str(project)) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["b"] == 2
