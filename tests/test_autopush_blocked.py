"""A scheduled memory push blocked by the credential check, end to end.

2026-09-30: the nightly push stopped at 04:18 on a false positive and the
only trace was a journal line; status still showed the previous success.
This drives the real push path in a scratch data repository.
"""

import os
import subprocess
import sys
from pathlib import Path

from data_repo_helpers import REPO_ROOT, configure_git_identity, run_git


def _data_repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    data = tmp_path / "data"
    subprocess.run(["git", "clone", "-q", str(remote), str(data)], check=True,
                   capture_output=True)
    configure_git_identity(data)
    (data / "claude").mkdir()
    (data / "claude" / "settings.json").write_text("{}", encoding="utf-8")
    (data / "memory" / "topics").mkdir(parents=True)
    (data / "memory" / "MEMORY.md").write_text("# index\n", encoding="utf-8")
    run_git(data, "add", ".")
    run_git(data, "commit", "-q", "-m", "init")
    run_git(data, "push", "-q", "origin", "HEAD")
    run_git(data, "branch", "--set-upstream-to", f"origin/{run_git(data, 'branch', '--show-current')}")
    return data


def _acg(data: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "XDG_STATE_HOME"}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "AI_CONFIG_REPO": str(data),
        "PYTHONPATH": str(REPO_ROOT), "AI_CONFIG_NO_UPDATE_CHECK": "1",
        "AI_CONFIG_NO_AUTOPUSH": "1",
    })
    return subprocess.run([sys.executable, "-m", "ai_config", *args],
                          capture_output=True, text=True, env=env, check=False)


def test_a_blocked_scheduled_push_is_reported_until_resolved(tmp_path: Path) -> None:
    data = _data_repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    note = data / "memory" / "topics" / "deploy.md"
    note.write_text("# deploy\n\nghp_" + "a" * 30 + "\n", encoding="utf-8")

    blocked = _acg(data, home, "memory", "push", "--if-stale", "0")

    assert blocked.returncode == 1
    status = _acg(data, home, "memory", "autopush", "status").stdout
    assert "上次自動推送失敗" in status
    assert "credential" in status
    assert "memory/topics/deploy.md" in status

    # 把內容拿掉之後沒有東西要推,失敗就不再回報
    note.unlink()
    run_git(data, "reset", "-q")
    assert _acg(data, home, "memory", "push", "--if-stale", "0").returncode == 0
    assert "上次自動推送失敗" not in _acg(data, home, "memory", "autopush", "status").stdout
