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
