"""Make the in-repo ai_config package importable regardless of whether (or
how) ai-config is installed — tests must not depend on a pip/pipx install."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


import pytest


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
    from ai_config import hooks, locking, memory, paths
    from ai_config.commands import memory as memory_command

    guard = tmp_path / "claude-home-guard"
    monkeypatch.setattr(memory, "CLAUDE_HOME", guard)
    monkeypatch.setattr(hooks, "CLAUDE_HOME", guard)
    # 在呼叫時才從 paths 讀的模組(例如列出正在跑的 Claude session)
    monkeypatch.setattr(paths, "CLAUDE_HOME", guard)
    monkeypatch.setattr(locking, "BACKUP_BASE", tmp_path / "backup-guard")
    monkeypatch.setattr(memory_command, "BACKUP_BASE", tmp_path / "backup-guard")
