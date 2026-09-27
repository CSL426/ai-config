"""Fake Drive clients and repositories shared by the Google Drive tests."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # CI runner 會設 XDG_CONFIG_HOME(Linux)/APPDATA(Windows),只 patch HOME
    # 不夠:config 會寫進 runner 的真實目錄並汙染後續 subprocess 測試。
    # AI_CONFIG_CONFIG 是 config_path() 的最高優先,直接鎖到本測試的 tmp。
    monkeypatch.setenv(
        "AI_CONFIG_CONFIG", str(tmp_path / "isolated" / "config.json")
    )
    monkeypatch.delenv("AI_CONFIG_REPO", raising=False)
