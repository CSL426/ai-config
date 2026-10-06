"""Reaching agents that run in herdr panes, Antigravity included."""

import pytest

from ai_config import messaging

AGENTS = {"result": {"agents": [
    {"agent": "agy", "agent_status": "idle", "cwd": "/w", "name": "agyy", "pane_id": "w1:p1"},
    {"agent": "codex", "agent_status": "blocked", "cwd": "/w", "pane_id": "w1:p2"},
]}}


@pytest.fixture
def herdr(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []

    def fake(*args, timeout=10.0):
        calls.append(args)
        if args[:2] == ("agent", "list"):
            return AGENTS
        if args[:2] == ("agent", "prompt"):
            return {"result": {"agent": {"agent_status": "idle"}}}
        return None

    monkeypatch.setattr(messaging, "_herdr", fake)
    for source in ("claude_peers", "codex_peers", "agy_peers"):
        monkeypatch.setattr(messaging, source, list)
    return calls


def test_herdr_agents_are_listed_and_only_a_blocked_one_cannot_receive(herdr) -> None:
    peers = {peer.id: peer for peer in messaging.list_peers()}
    agy = peers["w1:p1"]
    assert (agy.tool, agy.name, agy.via, agy.unreachable) == ("agy", "agyy", "herdr", "")
    # 停在確認畫面的 agent,herdr 會拒絕打字進去
    assert peers["w1:p2"].unreachable


def test_no_herdr_means_no_herdr_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messaging.shutil, "which", lambda name: None)
    assert messaging.herdr_peers() == []


def test_a_message_to_an_antigravity_in_herdr_is_typed_in_and_its_reply_read_back(
    herdr, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def screen(*args):
        body = next(call for call in herdr if call[:2] == ("agent", "prompt"))[3]
        first, *rest = body.split("\n")
        # agy 重印訊息時會換行,回答又剛好是訊息裡要求的那句話
        text = " ".join(rest)
        echoed = "\n".join(f"  {text[i:i + 20]}" for i in range(0, len(text), 20))
        return (f"> {first}\n{echoed}\n\n  AGY-OK add\n\n" + "─" * 40
                + "\n>\n" + "─" * 40 + "\nGemini 3.8 Flash | v1.2.17\n")

    monkeypatch.setattr(messaging, "_herdr_text", screen)
    peer, reply = messaging.send("agyy", "Reply with exactly one line: AGY-OK add",
                                 wait=60, sender="tester")

    assert peer.via == "herdr"
    prompt = next(call for call in herdr if call[:2] == ("agent", "prompt"))
    assert prompt[2] == "w1:p1"
    assert prompt[4:] == ("--wait", "--timeout", "60000")
    assert reply == "AGY-OK add"


def test_herdr_refusing_the_prompt_is_reported(monkeypatch: pytest.MonkeyPatch, herdr) -> None:
    monkeypatch.setattr(messaging, "_herdr", lambda *a, timeout=10.0: (
        AGENTS if a[:2] == ("agent", "list")
        else {"error": {"code": "agent_blocked", "message": "agent agyy is blocked"}}
    ))
    with pytest.raises(messaging.MessagingError, match="blocked"):
        messaging.send("agyy", "hello", sender="tester")
