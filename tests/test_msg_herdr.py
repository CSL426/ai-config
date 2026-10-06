"""Reaching agents that run in herdr panes, Antigravity included."""

import os
import subprocess
import sys

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


def _in_panes(monkeypatch: pytest.MonkeyPatch, panes: dict) -> None:
    monkeypatch.setattr(messaging, "_herdr_pane", lambda pid: panes.get(pid, ""))


def test_a_session_in_a_herdr_pane_is_listed_once_by_whichever_can_receive(
    herdr, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deaf = messaging.Peer("agy", "conv-1", "agyy", "", "", "open", pid=11, unreachable="收不到")
    hearing = messaging.Peer("codex", "thread-2", "cx", "set", "/w", "idle", pid=22)
    monkeypatch.setattr(messaging, "agy_peers", lambda: [deaf])
    monkeypatch.setattr(messaging, "codex_peers", lambda: [hearing])
    _in_panes(monkeypatch, {11: "w1:p1", 22: "w1:p2"})

    peers = messaging.list_peers()

    # 收不到的那份讓給 herdr;兩邊都收得到時留工具自己的管道
    assert [(p.tool, p.id, p.via) for p in peers] == [
        ("codex", "thread-2", ""), ("agy", "w1:p1", "herdr"),
    ]


def test_a_session_outside_herdr_stays_listed(herdr, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = messaging.Peer("agy", "conv-9", "other", "", "", "open", pid=33, unreachable="收不到")
    monkeypatch.setattr(messaging, "agy_peers", lambda: [outside])
    _in_panes(monkeypatch, {})

    assert [p.id for p in messaging.list_peers()] == ["conv-9", "w1:p1", "w1:p2"]


def _agy(via: str = "") -> messaging.Peer:
    return messaging.Peer("agy", "x", "", "herdr" if via else "", "", "open", via=via,
                          unreachable="" if via else "收不到")


@pytest.mark.parametrize(("peers", "outside"), [
    ([_agy()], True),
    ([_agy(), _agy("herdr")], False),  # 讀不到 /proc 時同一個 agy 會列兩次
    ([_agy(), _agy(), _agy("herdr")], True),
    ([_agy("herdr")], False),
])
def test_an_antigravity_outside_herdr_is_told_apart(peers: list, outside: bool) -> None:
    assert messaging.outside_herdr_agy(peers) is outside


@pytest.mark.parametrize("installed", [True, False])
def test_listing_an_antigravity_outside_herdr_says_how_to_reach_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, installed: bool,
) -> None:
    from ai_config.commands import msg

    monkeypatch.setattr(messaging, "list_peers", lambda: [_agy()])
    monkeypatch.setattr(msg.shutil, "which", lambda name: "/bin/herdr" if installed else None)

    assert msg.run_msg(["list"]) == 0

    out = capsys.readouterr()
    text = out.out + out.err
    assert "herdr" in text
    assert ("herdr.dev/install" in text) is not installed


def test_no_hint_when_every_antigravity_can_receive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    from ai_config.commands import msg

    monkeypatch.setattr(messaging, "list_peers", lambda: [_agy("herdr")])
    assert msg.run_msg(["list"]) == 0
    out = capsys.readouterr()
    assert "Antigravity" not in out.out + out.err


linux_only = pytest.mark.skipif(not os.path.exists("/proc/locks"), reason="needs /proc")


@linux_only
def test_the_holder_of_a_presence_lock_is_found(tmp_path) -> None:
    import fcntl

    lock = tmp_path / "conv.lock"
    lock.write_text("")
    with open(lock, "rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert messaging._lock_holder(lock) == os.getpid()
    assert messaging._lock_holder(lock) == 0


@linux_only
def test_the_herdr_pane_of_a_process_is_read_from_its_environment() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             env={**os.environ, "HERDR_PANE_ID": "w7:p3"})
    try:
        assert messaging._herdr_pane(child.pid) == "w7:p3"
    finally:
        child.kill()
        child.wait()
    assert messaging._herdr_pane(0) == ""


def test_what_antigravity_did_before_answering_is_not_the_reply() -> None:
    # 2026-10-06 對真的 agy 送測試訊息,它先讀檔、跑指令才回答
    body = "[acg 訊息 #482044,來自 tester]\nReply with exactly one line: DEDUP-OK"
    screen = "\n".join([
        "─" * 40,
        "> [acg 訊息 #482044,來自 tester]",
        "  Reply with exactly one line: DEDUP-OK",
        "",
        "▸ Thought for 5s, 254 tokens",
        "  先確認這個訊息怎麼送來的...",
        "",
        "● Read(~/.gemini/config/skills/acg/SKILL.md)",
        "● Bash(acg msg --help)",
        "● Read(~/ai-config/ai_config/messaging.py) (ctrl+o to expand)",
        "",
        "▸ Thought for 9s, 601 tokens",
        "  The system is receiving a test message and expects a specific single-line reply...",
        "  DEDUP-OK",
        "",
        "─" * 40,
        ">",
        "─" * 40,
        "Gemini 3.8 Flash (High) | v1.2.17",
    ])
    assert messaging._after_tag(screen, "482044", body) == "DEDUP-OK"
