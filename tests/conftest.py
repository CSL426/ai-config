"""Make the in-repo ai_config package importable regardless of whether (or
how) ai-config is installed — tests must not depend on a pip/pipx install."""

import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


import pytest

# 在任何 fixture 改 HOME 或 SCRIPT_DIR 之前就記下真的位置
_REAL_SETTINGS = (
    Path(os.environ.get("HOME", str(Path.home()))) / ".claude" / "settings.json"
)


def _real_data_git_config() -> Path:
    from ai_config import paths

    return paths.SCRIPT_DIR / ".git" / "config"


_GUARDED = (_REAL_SETTINGS, _real_data_git_config())


def _settings_fingerprint() -> "tuple[str | None, ...]":
    prints = []
    for path in _GUARDED:
        try:
            prints.append(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError:
            prints.append(None)
    return tuple(prints)


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_SETTINGS_KEY] = _settings_fingerprint()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run if it rewrote this machine's real settings or data repo.

    The fixtures below redirect the modules they know about, but a module
    that binds CLAUDE_HOME at import, or a subprocess that inherits HOME,
    slips past them. On 2026-09-24 that pointed every hook at a pytest
    temp directory, and on 2026-09-27 a refresh bound the real data
    repository's credential helper to one; neither failed a test. This
    turns both into a red run.
    """
    before = session.config.stash.get(_SETTINGS_KEY, ())
    changed = [
        path for path, old, new in zip(_GUARDED, before, _settings_fingerprint())
        if old != new
    ]
    if not changed:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    for path in changed:
        message = (
            f"this test run changed the real {path}; "
            "a test is writing outside its temporary home"
        )
        if reporter is not None:
            reporter.write_line(f"ERROR: {message}", red=True, bold=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


_SETTINGS_KEY = pytest.StashKey["tuple[str | None, ...]"]()


@pytest.fixture(autouse=True)
def _shared_table_stays_out_of_the_real_notebook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test reads and writes a throwaway schedule table.

    A test that called enable() without its own fixture once rewrote this
    machine's real slot (04:10 -> 04:00) and the change rode into the next
    push. Redirecting here means forgetting to patch cannot reach ~/.
    """
    from ai_config import schedule_table

    monkeypatch.setattr(schedule_table, "memory_dir", lambda: tmp_path / "table-memory")
    # patch 底層的 gethostname 而不是 host_name():host_name 自己也有測試要跑真的
    monkeypatch.setattr(schedule_table.socket, "gethostname", lambda: "test-host")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # systemd timer 的 stamp 檔在這裡;重排時會去碰它
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data-home"))


@pytest.fixture(autouse=True)
def _entrypoint_name_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """console_main and standalone_main write the name into os.environ.

    A test calling them in-process left "acg" behind, and every later
    subprocess test then printed acg where it expected ./ai-config.sh.
    """
    # setenv first so undo restores the variable to absent, not to ""
    monkeypatch.setenv("AI_CONFIG_ENTRYPOINT", "")
    monkeypatch.delenv("AI_CONFIG_ENTRYPOINT")


@pytest.fixture(autouse=True)
def _credential_binding_stays_out_of_the_data_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apply and update re-point the data repository's credential helper.

    In a test that repository is paths.SCRIPT_DIR, which is this machine's
    real one unless the test moved it; re-binding there would rewrite its
    git config. Tests of the refresh itself put the real one back.
    """
    from ai_config import hooks

    monkeypatch.setattr(hooks, "_refresh_credential_binding", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hooks and timers point at ~/.local/bin/ai-config when it exists.

    On a machine with acg installed that made the written commands depend
    on the machine running the tests, not on the test.
    """
    monkeypatch.setenv("AI_CONFIG_BIN_DIR", str(tmp_path / "no-launcher-bin"))


@pytest.fixture(autouse=True)
def _claude_settings_stay_out_of_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing a test does in-process may write ~/.claude or its backups.

    A test of `update` reached the hook repair it now ends with, and
    rewrote this machine's real settings.json to point every hook at a
    pytest temp directory. Tests that need a Claude home set their own,
    which overrides this.
    """
    from ai_config import hooks, locking, memory_paths, paths
    from ai_config.commands import memory_lifecycle

    guard = tmp_path / "claude-home-guard"
    monkeypatch.setattr(memory_paths, "CLAUDE_HOME", guard)
    monkeypatch.setattr(hooks, "CLAUDE_HOME", guard)
    # 在呼叫時才從 paths 讀的模組(例如列出正在跑的 Claude session)
    monkeypatch.setattr(paths, "CLAUDE_HOME", guard)
    monkeypatch.setattr(locking, "BACKUP_BASE", tmp_path / "backup-guard")
    monkeypatch.setattr(memory_lifecycle, "BACKUP_BASE", tmp_path / "backup-guard")
