"""Read-only lifecycle plans match real writes and use explicit projects."""

from pathlib import Path

import pytest
from test_memory_safety import isolated_memory, symlink  # noqa: F401

from ai_config import memory_index, memory_journal, memory_paths, memory_plan, safety
from ai_config.commands import memory as command
from ai_config.commands import memory_lifecycle


def tree(root: Path) -> dict:
    return {
        str(path.relative_to(root)): (
            ("link", str(path.readlink())) if path.is_symlink()
            else ("dir", None) if path.is_dir()
            else ("file", path.read_bytes())
        )
        for path in root.rglob("*")
    }


@pytest.fixture
def journal_project(isolated_memory, monkeypatch):  # noqa: F811
    _repo, home = isolated_memory
    project = home / "selected-project"
    project.mkdir()
    monkeypatch.setattr(memory_journal, "remember_installed", lambda: True)
    return project


def test_enable_preview_is_read_only_and_lists_source_and_shared_target(isolated_memory, monkeypatch):  # noqa: F811
    repo, home = isolated_memory
    monkeypatch.setattr(safety, "CLAUDE_HOME", memory_paths.CLAUDE_HOME)
    source = repo / "claude/CLAUDE.md"
    source.parent.mkdir()
    source.write_text("Source rules\n")
    live = memory_paths.live_rules_path()
    live.parent.mkdir()
    live.write_text("Local rules\n")
    symlink(memory_paths.codex_rules_path(), live)
    before = tree(repo.parent)
    plan = memory_plan.plan("enable")
    assert tree(repo.parent) == before
    assert any(c["destination"] == str(source) for c in plan.changes)
    shared = next(c for c in plan.changes if c["destination"] == str(memory_paths.codex_rules_path()))
    assert shared["shared"]
    assert shared["physical_target"] == str(live)
    assert "Claude" in shared["reason"]
    assert not (home / ".ai-config-backup").exists()


def test_adopt_preview_collision_names_match_execution(journal_project):
    root = journal_project
    target = memory_journal.project_journal_dir(memory_paths.project_key(root))
    target.mkdir(parents=True)
    (target / "recent.md").write_text("already shared")
    local = memory_journal.journal_link(root)
    local.mkdir(parents=True)
    (local / "recent.md").write_text("local")
    (local / ".gitignore").write_text("my original ignore")
    legacy = root / ".remember"
    legacy.mkdir()
    (legacy / "recent.md").write_text("legacy")
    before = tree(root.parent.parent)
    plan = memory_plan.plan("adopt", root)
    assert tree(root.parent.parent) == before
    expected = {
        Path(change["destination"])
        for change in plan.changes
        if change["operation"] == "move"
    }
    result = memory_lifecycle.execute("adopt", root)
    assert result.code == 0
    assert result.backup_path is not None
    assert all(path.is_file() for path in expected)
    assert {p.read_text() for p in target.glob("recent*")} == {
        "already shared", "local", "legacy"
    }
    assert "my original ignore" in {p.read_text() for p in target.glob(".gitignore*")}


def test_explicit_project_does_not_follow_cwd(journal_project, monkeypatch):
    selected = journal_project
    other = selected.parent / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert memory_lifecycle.execute("adopt", selected).code == 0
    assert memory_journal.journal_state(selected)[0] == "adopted"
    assert memory_journal.journal_state(other)[0] == "none"
    assert Path.cwd() == other


def test_release_works_after_plugin_removed(journal_project, monkeypatch):
    root = journal_project
    assert memory_lifecycle.execute("adopt", root).code == 0
    monkeypatch.setattr(memory_journal, "remember_installed", lambda: False)
    before = tree(root.parent.parent)
    plan = memory_plan.plan("release", root)
    assert tree(root.parent.parent) == before
    assert any("其他機器" in warning for warning in plan.warnings)
    assert memory_lifecycle.execute("release", root).code == 0
    assert not safety.is_reparse_point(memory_journal.journal_link(root))


@pytest.mark.parametrize("action,project", [("adopt", None), ("release", None), ("enable", Path(".")), ("disable", Path(".")), ("bogus", None)])
def test_invalid_scope_rejected_without_backups(isolated_memory, action, project):  # noqa: F811
    repo, _home = isolated_memory
    before = tree(repo.parent)
    with pytest.raises(ValueError):
        memory_plan.plan(action, project)
    assert tree(repo.parent) == before


def test_rollback_retains_external_rule_change_and_exposes_backup(isolated_memory, monkeypatch):  # noqa: F811
    _repo, _home = isolated_memory
    live = memory_paths.live_rules_path()
    live.parent.mkdir()
    live.write_text("original\n")

    def fail_after_external_edit():
        live.write_text("external edit\n")
        raise OSError("injected failure")

    monkeypatch.setattr(memory_paths, "install_agy_rules", fail_after_external_edit)
    with pytest.raises(memory_lifecycle.MemoryOperationError) as caught:
        memory_lifecycle.execute("enable")
    assert caught.value.recovery_required
    assert caught.value.backup_path is not None
    assert live.read_text() == "external edit\n"
    assert not memory_paths.MEMORY_LINK.exists()


def test_external_journal_edit_is_not_moved_during_rollback(journal_project, monkeypatch):
    root = journal_project
    local = memory_journal.journal_link(root)
    local.mkdir(parents=True)
    (local / "recent.md").write_text("original")
    target = memory_journal.project_journal_dir(memory_paths.project_key(root))

    def fail(_target, _link):
        (target / "recent.md").write_text("external edit")
        raise OSError("injected link failure")

    monkeypatch.setattr(memory_journal, "_create_journal_link", fail)
    with pytest.raises(memory_lifecycle.MemoryOperationError) as caught:
        memory_lifecycle.execute("adopt", root)
    assert caught.value.recovery_required
    assert caught.value.backup_path is not None
    assert (target / "recent.md").read_text() == "external edit"
    assert not (local / "recent.md").exists()


def test_entry_status_rejects_unmanaged_link(isolated_memory):  # noqa: F811
    repo, _home = isolated_memory
    external = repo / "external.md"
    external.write_text(memory_paths.RULES_BLOCK)
    symlink(memory_paths.codex_rules_path(), external)
    state = memory_index.entry_status(memory_paths.codex_rules_path())
    assert state["status"] == "blocked"


def test_adopt_takes_a_path_without_changing_directory(journal_project, monkeypatch):
    """cd-ing into each project in turn is the whole complaint.

    execute() has always taken a project; only the CLI insisted on the
    working directory.
    """
    elsewhere = journal_project.parent / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert command.run_memory(["adopt", str(journal_project)]) == 0

    assert memory_journal.journal_state(journal_project)[0] == "adopted"
    assert Path.cwd() == elsewhere


def test_adopt_refuses_a_path_that_is_not_a_directory(tmp_path):
    assert command.run_memory(["adopt", str(tmp_path / "nope")]) == 1


def test_scan_finds_every_journal_under_a_root(journal_project, tmp_path):
    """Anywhere with a journal is a candidate; the depth is not knowable."""
    from ai_config import memory_index

    deep = tmp_path / "work" / "nested" / "deep"
    (deep / ".remember").mkdir(parents=True)
    (deep / ".remember" / "recent.md").write_text("notes", encoding="utf-8")

    found = memory_index.journals_below(tmp_path)

    assert deep in found


def test_scan_finds_a_project_whose_journal_remember_already_linked(journal_project):
    """remember makes .remember a link to memory/journal/<slug> on its own.

    That project is "local" and waiting for adopt, but the scan skipped every
    link before looking at its name, so project-a never showed up.
    """
    from ai_config import memory_index

    local = memory_journal.journal_link(journal_project)
    local.mkdir(parents=True)
    (local / "recent.md").write_text("notes", encoding="utf-8")
    symlink(journal_project / ".remember", local, directory=True)

    assert memory_journal.journal_state(journal_project)[0] == "local"
    assert journal_project in memory_index.unadopted_below(journal_project.parent)


def _local_journal(project):
    local = memory_journal.journal_link(project)
    local.mkdir(parents=True)
    (local / "recent.md").write_text("notes", encoding="utf-8")
    symlink(project / ".remember", local, directory=True)


def _memory_on(monkeypatch):
    monkeypatch.setattr(memory_paths, "link_state", lambda: ("ok", ""))
    monkeypatch.setattr(memory_journal, "journal_config_state", lambda: ("ours", ""))


def test_projects_with_a_journal_are_adopted_before_the_nightly_push(journal_project, monkeypatch):
    """Anywhere a session has worked is a project; nobody should have to scan for it."""
    _local_journal(journal_project)
    _memory_on(monkeypatch)
    monkeypatch.setattr(
        memory_paths, "project_key",
        lambda root: memory_paths.ProjectKey("owner--repo", True, str(root)),
    )
    adopted = []
    monkeypatch.setattr(
        memory_lifecycle, "execute",
        lambda action, project: adopted.append((action, project)) or memory_lifecycle.MemoryExecutionResult(code=0),
    )

    lines = memory_lifecycle.adopt_touched(journal_project.parent)

    assert adopted == [("adopt", journal_project)]
    assert "已同步專案日誌" in lines[0]


def test_a_project_without_a_remote_is_listed_not_adopted(journal_project, monkeypatch):
    """Its key is the folder name; two machines' unrelated ~/test would merge."""
    _local_journal(journal_project)
    _memory_on(monkeypatch)
    monkeypatch.setattr(
        memory_paths, "project_key",
        lambda root: memory_paths.ProjectKey(root.name, False, str(root)),
    )
    monkeypatch.setattr(memory_lifecycle, "execute", lambda *a, **k: pytest.fail("不該自動同步"))

    lines = memory_lifecycle.adopt_touched(journal_project.parent)

    assert "沒有 git 遠端" in lines[0]


def test_nothing_is_adopted_where_memory_is_off_or_opted_out(journal_project, monkeypatch):
    _local_journal(journal_project)
    monkeypatch.setattr(memory_lifecycle, "execute", lambda *a, **k: pytest.fail("不該動"))
    monkeypatch.setattr(memory_paths, "link_state", lambda: ("missing", ""))

    assert memory_lifecycle.adopt_touched(journal_project.parent) == []

    _memory_on(monkeypatch)
    monkeypatch.setenv("AI_CONFIG_NO_AUTO_ADOPT", "1")

    assert memory_lifecycle.adopt_touched(journal_project.parent) == []


def test_scan_skips_what_is_already_adopted(journal_project, monkeypatch):
    from ai_config import memory_index

    root = journal_project.parent
    memory_lifecycle.execute("adopt", journal_project)

    assert journal_project not in memory_index.unadopted_below(root)


def test_scan_leaves_the_home_directory_and_data_repo_alone(tmp_path, monkeypatch):
    """Neither is a project: one is where projects live, the other is the store.

    A scan that offers to adopt the data repository into itself is offering
    to nest the notebook inside its own journal.
    """
    from ai_config import memory_index, memory_paths

    monkeypatch.setattr(memory_paths, "HOME", tmp_path)
    (tmp_path / ".remember").mkdir()
    (tmp_path / ".remember" / "recent.md").write_text("home", encoding="utf-8")
    store = tmp_path / "ai-config" / "data"
    (store / ".remember").mkdir(parents=True)
    (store / ".remember" / "recent.md").write_text("store", encoding="utf-8")
    monkeypatch.setattr(memory_paths, "SCRIPT_DIR", store)

    found = memory_index.journals_below(tmp_path)

    assert tmp_path not in found
    assert store not in found


def test_status_counts_the_projects_still_waiting(
    journal_project, monkeypatch, capsys
):
    """A count is what makes the scan discoverable; nobody runs --scan blind."""
    from ai_config import memory_paths

    monkeypatch.setattr(memory_paths, "HOME", journal_project.parent)
    monkeypatch.chdir(journal_project)
    # 要數的是「這個以外」的專案,所以得真的有第二個
    other = journal_project.parent / "another-project"
    (other / ".remember").mkdir(parents=True)
    (other / ".remember" / "recent.md").write_text("notes", encoding="utf-8")

    memory_lifecycle._report_unadopted()

    assert "adopt all" in capsys.readouterr().out


def test_adopt_all_means_the_same_as_scan(journal_project, monkeypatch, capsys):
    """Every other command takes `all`, so this one reads like it should.

    `adopt all` reported "找不到這個專案目錄:all" — it had taken the word
    as a path. --scan is the flag, but nobody guesses a flag when the
    word already means this everywhere else.
    """
    from ai_config import memory_paths

    monkeypatch.setattr(memory_paths, "HOME", journal_project.parent)
    other = journal_project.parent / "another-project"
    (other / ".remember").mkdir(parents=True)
    (other / ".remember" / "recent.md").write_text("notes", encoding="utf-8")
    monkeypatch.setattr("ai_config.console.confirm", lambda *a, **k: False)

    assert command.run_memory(["adopt", "all"]) == 0

    assert "尚未同步的專案" in capsys.readouterr().out


def test_a_subdirectory_journal_moves_into_its_repository(journal_project):
    """Windows: For_spark/.remember inside the adopted GL repo was 'synced' nightly and never moved."""
    import subprocess

    root = journal_project
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert memory_lifecycle.execute("adopt", root).code == 0
    target = memory_journal.project_journal_dir(memory_paths.project_key(root))
    (target / "today-2026-07-30.md").write_text("root notes")
    nested = root / "references" / "For_spark"
    legacy = nested / ".remember"
    legacy.mkdir(parents=True)
    (legacy / ".gitignore").write_text("*\n")
    (legacy / "today-2026-07-30.md").write_text("nested notes")
    assert nested in memory_index.unadopted_below(root.parent)

    before = tree(root.parent.parent)
    plan = memory_plan.plan("adopt", nested)
    assert tree(root.parent.parent) == before
    expected = {Path(c["destination"]) for c in plan.changes if c["operation"] == "move"}
    result = memory_lifecycle.execute("adopt", nested)

    assert result.code == 0 and result.changed
    assert expected and all(path.is_file() for path in expected)
    # 同名的兩份都留著
    assert {p.read_text() for p in target.glob("today-2026-07-30*")} == {"root notes", "nested notes"}
    assert (legacy / memory_journal.MIGRATED_NOTE).is_file()
    assert nested not in memory_index.unadopted_below(root.parent)
    assert memory_lifecycle.execute("adopt", nested).changed is False


def test_nothing_to_adopt_is_not_reported_as_synced(journal_project, monkeypatch):
    _local_journal(journal_project)
    _memory_on(monkeypatch)
    monkeypatch.setattr(
        memory_paths, "project_key",
        lambda root: memory_paths.ProjectKey("owner--repo", True, str(root)),
    )
    monkeypatch.setattr(
        memory_lifecycle, "execute",
        lambda action, project: memory_lifecycle.MemoryExecutionResult(code=0, changed=False),
    )

    assert memory_lifecycle.adopt_touched(journal_project.parent) == []


def test_adopt_from_the_subdirectory_reaches_its_old_journal(journal_project, monkeypatch, capsys):
    import subprocess

    root = journal_project
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert memory_lifecycle.execute("adopt", root).code == 0
    nested = root / "sub"
    (nested / ".remember").mkdir(parents=True)
    (nested / ".remember" / "today-2026-07-30.md").write_text("nested notes")

    assert command.run_memory(["adopt", "--help"]) == 0
    assert "找不到" not in capsys.readouterr().out
    monkeypatch.chdir(nested)
    assert command.run_memory(["adopt"]) == 0

    target = memory_journal.project_journal_dir(memory_paths.project_key(root))
    assert (target / "today-2026-07-30.md").read_text() == "nested notes"


def test_remembers_own_logs_stay_under_the_ignored_names(journal_project):
    """A4000: moved aside as logs.from-api they fell outside .gitignore, ready to be pushed."""
    import subprocess

    root = journal_project
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert memory_lifecycle.execute("adopt", root).code == 0
    target = memory_journal.project_journal_dir(memory_paths.project_key(root))
    for name in memory_journal.RUNTIME_DIRS:
        (target / name).mkdir(exist_ok=True)
    nested = root / "api"
    legacy = nested / ".remember"
    (legacy / "logs").mkdir(parents=True)
    (legacy / "logs" / "memory-2026-07-07.log").write_text("plugin log")
    (legacy / "tmp").mkdir()
    (legacy / "tmp" / "save-session.pid").write_text("42")
    (legacy / "today-2026-07-07.md").write_text("notes")

    planned = {
        Path(c["destination"]) for c in memory_plan.plan("adopt", nested).changes
        if c["operation"] == "move"
    }
    assert memory_lifecycle.execute("adopt", nested).code == 0

    assert planned and all(path.exists() for path in planned)
    assert (target / "logs" / "from-api" / "memory-2026-07-07.log").read_text() == "plugin log"
    assert (target / "tmp" / "from-api" / "save-session.pid").is_file()
    assert not list(target.glob("*.from-*"))
    ignored = (target / ".gitignore").read_text().split()
    assert {"logs/", "tmp/"} <= set(ignored)
