"""remember on Codex and Antigravity: detected, installed and removed by acg."""

import json
from pathlib import Path

import pytest

from ai_config import memory
from ai_config import remember_hosts as hosts


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    codex = tmp_path / ".codex"
    codex.mkdir()
    monkeypatch.setattr(hosts, "CODEX_HOME", codex)
    monkeypatch.setattr(hosts, "AGY_HOOKS", tmp_path / ".gemini" / "config" / "hooks.json")
    cache = tmp_path / "claude-cache" / "remember"
    monkeypatch.setattr(memory, "REMEMBER_PLUGIN_CACHE", cache)
    # CI 上沒有 codex / agy 指令;測試談的是 acg 的行為,不是那台有什麼
    monkeypatch.setattr(hosts, "available", lambda host: True)
    monkeypatch.setattr(hosts, "_codex_has_plugins", lambda: True)
    for version in ("0.9.0", "0.32.0", "0.10.0"):
        scripts = cache / version / "scripts"
        scripts.mkdir(parents=True)
        for name in hosts._AGY_EVENT_SCRIPTS.values():
            (scripts / name).write_text("#!/bin/bash\n")
    return tmp_path


def _codex_config(tmp_path: Path, text: str) -> None:
    (tmp_path / ".codex" / "config.toml").write_text(text, encoding="utf-8")


def _codex_cache(tmp_path: Path, version: str) -> None:
    (tmp_path / ".codex" / "plugins" / "cache" / "remember-dev" / "remember" / version).mkdir(parents=True)


def test_newest_version_wins_numerically(homes: Path) -> None:
    assert hosts.claude_plugin_root().name == "0.32.0"


def test_codex_absent_until_config_and_cache_agree(homes: Path) -> None:
    assert hosts.codex_state() == hosts.HostState(installed=False)
    _codex_config(homes, '[plugins."remember@remember-dev"]\nenabled = true\n')
    assert hosts.codex_state().installed is False
    assert "快取" in hosts.codex_state().detail
    _codex_cache(homes, "0.33.0")
    state = hosts.codex_state()
    assert (state.installed, state.version, state.trusted) == (True, "0.33.0", False)


def test_codex_trust_is_read_from_the_hook_state(homes: Path) -> None:
    _codex_cache(homes, "0.33.0")
    _codex_config(homes, (
        '[plugins."remember@remember-dev"]\nenabled = true\n'
        '[hooks.state]\n'
        '[hooks.state."remember@remember-dev:hooks/hooks.codex.json:session_start:0:0"]\n'
        'trusted_hash = "sha256:abc"\n'
    ))
    assert hosts.codex_state().trusted is True


def test_codex_install_adds_marketplace_once_and_says_trust(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(*args):
        calls.append(args)
        if args == ("add", "remember@remember-dev"):
            _codex_config(homes, '[marketplaces.remember-dev]\nsource = "x"\n[plugins."remember@remember-dev"]\nenabled = true\n')
            _codex_cache(homes, "0.33.0")
        elif args[:2] == ("marketplace", "add"):
            _codex_config(homes, '[marketplaces.remember-dev]\nsource = "x"\n')

    monkeypatch.setattr(hosts, "_run_codex", fake_run)
    lines = hosts.install_codex()
    assert calls == [("marketplace", "add", hosts.CODEX_MARKETPLACE_REPO), ("add", "remember@remember-dev")]
    assert any("/hooks" in line for line in lines)
    assert hosts.install_codex() == [
        "Codex 會先審核新 hook 才執行:開一次 codex,輸入 /hooks 看過 remember 的 hook 就算信任"
    ]
    assert len(calls) == 2


def test_codex_remove_drops_plugin_then_marketplace(
    homes: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _codex_cache(homes, "0.33.0")
    _codex_config(homes, '[marketplaces.remember-dev]\nsource = "x"\n[plugins."remember@remember-dev"]\nenabled = true\n')
    calls = []
    monkeypatch.setattr(hosts, "_run_codex", lambda *a: calls.append(a))
    hosts.remove_codex()
    assert calls == [("remove", "remember@remember-dev"), ("marketplace", "remove", "remember-dev")]


def test_missing_codex_binary_is_a_clear_error(homes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosts.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="找不到 codex"):
        hosts.install_codex()


def test_agy_install_keeps_other_plugins_and_remove_leaves_them(homes: Path) -> None:
    hosts.AGY_HOOKS.parent.mkdir(parents=True)
    hosts.AGY_HOOKS.write_text(json.dumps({"other": {"enabled": True, "Stop": []}}))
    assert hosts.agy_state().installed is False

    assert hosts.install_agy()
    data = json.loads(hosts.AGY_HOOKS.read_text())
    assert set(data) == {"other", "remember"}
    assert data["remember"]["enabled"] is True
    assert set(data["remember"]) == {"enabled", *hosts._AGY_EVENT_SCRIPTS}
    command = data["remember"]["SessionStart"][0]["command"]
    assert command.startswith("bash ") and "0.32.0/scripts/agy-session-start-hook.sh" in command
    assert hosts.agy_state() == hosts.HostState(installed=True, version="0.32.0")
    assert hosts.install_agy() == []  # 已經一樣就不重寫

    assert hosts.remove_agy()
    assert json.loads(hosts.AGY_HOOKS.read_text()) == {"other": {"enabled": True, "Stop": []}}
    assert hosts.remove_agy() == []


def test_agy_entry_points_at_missing_scripts_is_reported(homes: Path) -> None:
    hosts.install_agy()
    for script in (memory.REMEMBER_PLUGIN_CACHE / "0.32.0" / "scripts").iterdir():
        script.unlink()
    state = hosts.agy_state()
    assert state.installed is True and "腳本不存在" in state.detail


def test_agy_without_claude_remember_cannot_install(homes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory, "REMEMBER_PLUGIN_CACHE", homes / "nowhere")
    with pytest.raises(RuntimeError, match="沒有 remember plugin"):
        hosts.install_agy()


def test_corrupt_agy_hooks_file_is_never_overwritten(homes: Path) -> None:
    hosts.AGY_HOOKS.parent.mkdir(parents=True)
    hosts.AGY_HOOKS.write_text("[1, 2]")
    assert "不是物件" in hosts.agy_state().detail
    with pytest.raises(RuntimeError):
        hosts.install_agy()
    assert hosts.AGY_HOOKS.read_text() == "[1, 2]"


def test_status_reports_each_host(homes: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ai_config.commands import memory as command

    _codex_cache(homes, "0.33.0")
    _codex_config(homes, '[plugins."remember@remember-dev"]\nenabled = true\n')
    command._report_hosts()
    out = capsys.readouterr().out
    assert "Codex 已裝 remember 0.33.0" in out and "/hooks" in out
    assert "Antigravity 尚未裝 remember" in out


def test_enable_and_disable_one_host_through_the_cli(homes: Path) -> None:
    from ai_config.commands import memory as command

    assert command.run_memory(["enable", "agy"]) == 0
    assert hosts.agy_state().installed is True
    assert command.run_memory(["disable", "agy"]) == 0
    assert hosts.agy_state().installed is False


def test_hosts_without_the_cli_are_neither_reported_nor_offered(
    homes: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_config.commands import memory as command

    monkeypatch.setattr(hosts, "available", lambda host: host != "agy")
    command._report_hosts()
    out = capsys.readouterr().out
    assert "Codex" in out and "Antigravity" not in out
    assert command.run_memory(["enable", "agy"]) == 1
    assert hosts.agy_state().installed is False


def test_old_codex_without_plugins_is_explained(homes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosts, "_codex_has_plugins", lambda: False)
    monkeypatch.setattr(hosts, "codex_version", lambda: "codex-cli 0.77.0")
    with pytest.raises(RuntimeError, match="0.77.0.*沒有 plugin"):
        hosts.install_codex()
