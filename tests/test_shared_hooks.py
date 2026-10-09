"""Hooks written once in Claude Code's format, projected into Codex and Antigravity."""

import io
import json
import shlex
import sys
from pathlib import Path

import pytest

from ai_config import hooks, paths, remember_hosts, shared_hooks

PUSH = shared_hooks.SharedHook(
    name="after-push", event="PostToolUse", command='bash "$HOME/x.sh"', matcher="Bash", timeout=5,
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    (data / "claude").mkdir(parents=True)
    monkeypatch.setattr(paths, "SCRIPT_DIR", data)
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir()
    monkeypatch.setattr(hooks, "settings_path", lambda: settings)
    homes = [tmp_path / ".codex", tmp_path / ".codex-work"]
    for home in homes:
        home.mkdir()
    monkeypatch.setattr(shared_hooks, "codex_homes", lambda: homes)
    agy = tmp_path / ".gemini" / "config" / "hooks.json"
    monkeypatch.setattr(remember_hosts, "AGY_HOOKS", agy)
    monkeypatch.setattr(paths, "scheduled_command", lambda: ["/opt/acg/ai-config"])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def _entries(document: dict, event: str) -> list:
    return [h for row in document.get("hooks", {}).get(event, []) for h in row["hooks"]]


def test_a_definition_round_trips_and_odd_names_are_refused(world: Path) -> None:
    shared_hooks.write_definition(PUSH)

    assert shared_hooks.load_all() == [PUSH]
    for name in ("../escape", "inject"):
        with pytest.raises(ValueError, match="名稱"):
            shared_hooks.write_definition(shared_hooks.SharedHook(name, "PostToolUse", "x"))
    # 拒絕之前不能先寫出任何東西
    assert sorted(p.name for p in world.rglob("*.json")) == ["after-push.json"]


def test_claude_gets_one_marked_copy_and_keeps_everything_else(world: Path) -> None:
    shared_hooks.write_definition(PUSH)
    mine = {"type": "command", "command": "echo mine"}
    acg_own = {"type": "command", "command": "acg", "statusMessage": "acg：commit 訊息風格"}
    stale = {"type": "command", "command": "old", "statusMessage": "acg：共用 gone"}
    document = {"hooks": {"PostToolUse": [{"hooks": [mine]}, {"hooks": [acg_own, stale]}]}}

    once = shared_hooks.project_claude(document)

    entries = _entries(once, "PostToolUse")
    assert mine in entries and acg_own in entries and stale not in entries
    shared = [e for e in entries if e.get("statusMessage", "").startswith("acg：共用")]
    assert shared == [{"type": "command", "command": PUSH.command, "timeout": 5,
                       "statusMessage": "acg：共用 after-push"}]
    assert shared_hooks.project_claude(once) == once


def test_apply_writes_it_and_gathering_never_takes_it_back(world: Path) -> None:
    from ai_config.tools.claude import filter_claude_settings, merge_claude_settings

    shared_hooks.write_definition(PUSH)
    source = json.dumps({"theme": "dark"})
    target = json.dumps({"theme": "light", "hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": "echo mine"}]}]}})

    applied = json.loads(merge_claude_settings(source, target))

    assert any(e.get("statusMessage") == "acg：共用 after-push" for e in _entries(applied, "PostToolUse"))
    gathered = json.loads(filter_claude_settings(json.dumps(applied)))
    # 資料庫只存定義;投影出去的那份不能被收回去,否則每台機器各多一份
    assert all(e["command"] != PUSH.command for e in _entries(gathered, "PostToolUse"))


def test_every_codex_home_gets_it_and_its_other_hooks_stay(world: Path) -> None:
    shared_hooks.write_definition(PUSH)
    shared_hooks.write_definition(shared_hooks.SharedHook("on-stop", "Stop", "echo stop"))
    theirs = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "g"}]}]}}
    (world / ".codex" / "hooks.json").write_text(json.dumps(theirs), encoding="utf-8")

    changed = shared_hooks.project_codex()

    assert len(changed) == 2
    for home in (".codex", ".codex-work"):
        document = json.loads((world / home / "hooks.json").read_text(encoding="utf-8"))
        assert [e["command"] for e in _entries(document, "PostToolUse")] == [PUSH.command]
        # Codex 沒有 Stop;寫進去也不會跑,只會讓人以為裝好了
        assert "Stop" not in document["hooks"]
    first = json.loads((world / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert first["hooks"]["PreToolUse"] == theirs["hooks"]["PreToolUse"]
    assert shared_hooks.project_codex() == []


def test_a_removed_definition_leaves_every_tool(world: Path) -> None:
    shared_hooks.write_definition(PUSH)
    shared_hooks.project_codex()
    shared_hooks.project_agy()

    shared_hooks.delete_definition(PUSH.name)
    shared_hooks.project_codex()
    shared_hooks.project_agy()

    document = json.loads((world / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert _entries(document, "PostToolUse") == []
    agy = json.loads(remember_hosts.AGY_HOOKS.read_text(encoding="utf-8"))
    assert not any(key.startswith("acg-") for key in agy)


def test_no_hooks_file_is_created_for_nothing(world: Path) -> None:
    assert shared_hooks.project_codex() == []
    assert not (world / ".codex" / "hooks.json").exists()


def test_antigravity_gets_its_own_shape_through_the_adapter(world: Path) -> None:
    shared_hooks.write_definition(PUSH)
    shared_hooks.write_definition(shared_hooks.SharedHook("start", "SessionStart", "echo hi"))
    shared_hooks.write_definition(shared_hooks.SharedHook("edits", "PostToolUse", "x", matcher="Edit|Write"))
    remember_hosts.AGY_HOOKS.parent.mkdir(parents=True)
    remember_hosts.AGY_HOOKS.write_text(json.dumps({
        "remember": {"enabled": True},
        # 別人取的 acg- 開頭的項目不是我們的
        "acg-someone-else": {"enabled": True, "Stop": [{"type": "command", "command": "x"}]},
    }), encoding="utf-8")

    shared_hooks.project_agy()

    agy = json.loads(remember_hosts.AGY_HOOKS.read_text(encoding="utf-8"))
    assert agy["remember"] == {"enabled": True}
    assert "acg-someone-else" in agy
    group = agy["acg-after-push"]["PostToolUse"][0]
    assert group["matcher"] == "run_command"
    assert shlex.split(group["hooks"][0]["command"]) == ["/opt/acg/ai-config", "__agy-hook", "after-push"]
    assert group["hooks"][0]["timeout"] > PUSH.timeout
    # 非工具事件在 Antigravity 是扁平的一串
    assert agy["acg-start"]["SessionStart"][0]["type"] == "command"
    # 對不到 Antigravity 工具名的 matcher 不投影
    assert "acg-edits" not in agy
    assert "PreInvocation" in agy["acg-inject"]


def test_what_a_tool_cannot_run_is_said() -> None:
    edits = shared_hooks.SharedHook("e", "PostToolUse", "x", matcher="Edit")
    only_codex = shared_hooks.SharedHook("c", "PostToolUse", "x", to="codex")

    assert "Bash" in edits.unsupported("agy")
    assert edits.unsupported("codex") == ""
    # 沒限定工具的 hook 在 Antigravity 會收到沒轉換過的 payload
    assert shared_hooks.SharedHook("all", "PostToolUse", "x", matcher="*").unsupported("agy")
    # 擋工具的格式沒有文件,投過去會擋不住
    assert shared_hooks.SharedHook("p", "PreToolUse", "x", matcher="Bash").unsupported("agy")
    assert only_codex.unsupported("agy") and not only_codex.unsupported("codex")
    assert shared_hooks.SharedHook("s", "Stop", "x").unsupported("codex")


def test_an_antigravity_payload_reads_like_claude_codes() -> None:
    agy = {
        "conversationId": "c-1", "workspacePaths": ["/w"], "transcriptPath": "/t",
        "toolCall": {"name": "run_command", "args": {"CommandLine": "git push", "Cwd": "/w/repo"}},
    }

    payload = shared_hooks.claude_payload(agy, "PostToolUse")

    assert payload == {
        "hook_event_name": "PostToolUse", "session_id": "c-1", "cwd": "/w/repo",
        "transcript_path": "/t", "tool_name": "Bash", "tool_input": {"command": "git push"},
    }
    assert "tool_response" not in payload


def _call(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, args: list, payload) -> dict:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    assert shared_hooks.run_agy_hook(args) == 0
    return json.loads(capsys.readouterr().out)


def test_the_adapter_hands_the_reply_to_the_next_step(
    world: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    script = world / "hook.py"
    script.write_text(
        "import json, sys\n"
        "p = json.load(sys.stdin)\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse',"
        " 'additionalContext': 'saw ' + p['tool_input']['command']}}))\n",
        encoding="utf-8",
    )
    # 斜線在 Git Bash 與 POSIX shell 都認得
    command = f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(script.as_posix())}"
    shared_hooks.write_definition(shared_hooks.SharedHook("probe", "PostToolUse", command, matcher="Bash"))
    agy = {"conversationId": "c-9", "workspacePaths": [str(world)],
           "toolCall": {"name": "run_command", "args": {"CommandLine": "git push"}}}
    shared_hooks.project_agy()
    # apply 之後才改的定義,不 apply 就不會在 Antigravity 跑
    shared_hooks.write_definition(shared_hooks.SharedHook("probe", "PostToolUse", "echo changed", matcher="Bash"))

    # PostToolUse 只能回 {};提醒要等下一步開始前才送得進去
    assert _call(monkeypatch, capsys, ["probe"], agy) == {}
    injected = _call(monkeypatch, capsys, ["--inject"], {"conversationId": "c-9"})

    assert injected == {"injectSteps": [{"ephemeralMessage": "saw git push"}]}
    assert _call(monkeypatch, capsys, ["--inject"], {"conversationId": "c-9"}) == {}


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]"])
def test_the_adapter_never_fails_the_turn(
    world: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, stdin: str,
) -> None:
    assert _call(monkeypatch, capsys, ["missing"], stdin) == {}
    assert _call(monkeypatch, capsys, ["--inject"], stdin) == {}


def test_sharing_moves_a_claude_hook_and_unsharing_puts_it_back(
    world: Path, capsys: pytest.CaptureFixture,
) -> None:
    from ai_config.commands.hooks import run_hooks

    original = {"type": "command", "command": 'bash "$HOME/x.sh"', "timeout": 5}
    hooks.write_settings({"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [original]}]}})

    assert run_hooks(["share"]) == 0
    assert 'bash "$HOME/x.sh"' in capsys.readouterr().out
    assert run_hooks(["share", "1", "--name", "after-push"]) == 0

    assert shared_hooks.load_all() == [PUSH]
    entries = _entries(hooks.read_settings(), "PostToolUse")
    assert [e.get("statusMessage") for e in entries] == ["acg：共用 after-push"]

    assert run_hooks(["unshare", "after-push"]) == 0
    assert shared_hooks.load_all() == []
    assert _entries(hooks.read_settings(), "PostToolUse") == [original]


def test_sharing_refuses_a_taken_name_and_a_bad_number(world: Path) -> None:
    from ai_config.commands.hooks import run_hooks

    hooks.write_settings({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}})
    shared_hooks.write_definition(PUSH)

    assert run_hooks(["share", "1", "--name", "after-push"]) == 1
    assert run_hooks(["share", "7", "--name", "other"]) == 1
    assert run_hooks(["share", "1", "--name", "other", "--to", "everywhere"]) == 1


def test_codex_trust_is_read_never_written(world: Path) -> None:
    home = world / ".codex"
    shared_hooks.write_definition(PUSH)
    shared_hooks.project_codex()
    assert not shared_hooks.codex_trusted(home, PUSH)
    other = f"{home / 'hooks.json'}:post_tool_use:5:0"
    (home / "config.toml").write_text(f"[hooks.state.'{other}']\ntrusted_hash = 'sha256:x'\n", encoding="utf-8")
    # 同事件別的位置有信任紀錄,不代表這個 hook 被看過
    assert not shared_hooks.codex_trusted(home, PUSH)
    key = f"{home / 'hooks.json'}:post_tool_use:0:0"
    # 單引號是 TOML 的字面字串:Windows 路徑的反斜線不會被當成跳脫字元
    (home / "config.toml").write_text(
        f"[hooks.state.'{key}']\ntrusted_hash = 'sha256:abc'\n", encoding="utf-8",
    )

    before = (home / "config.toml").read_bytes()
    assert shared_hooks.codex_trusted(home, PUSH)
    assert (home / "config.toml").read_bytes() == before


def test_the_desktop_app_sees_what_each_tool_gets(world: Path) -> None:
    from ai_config.gui_management import _shared_hooks_state

    shared_hooks.write_definition(PUSH)
    shared_hooks.write_definition(shared_hooks.SharedHook("on-stop", "Stop", "echo stop"))
    shared_hooks.project_codex()

    state = {item["name"]: item for item in _shared_hooks_state()}

    assert state["after-push"]["codex"] == "" and state["after-push"]["agy"] == ""
    assert state["after-push"]["codex_untrusted"] == [".codex", ".codex-work"]
    assert "Codex" in state["on-stop"]["codex"]


@pytest.mark.parametrize("content", [
    "not json", '{"event": "PostToolUse"}', '{"event": "PostToolUse", "command": "x", "to": "everyone"}',
    '{"event": "PostToolUse", "command": "x", "options": {"args": []}}',
])
def test_a_broken_definition_stops_apply_instead_of_removing_the_hook(world: Path, content: str) -> None:
    shared_hooks.write_definition(PUSH)
    shared_hooks.project_codex()
    before = (world / ".codex" / "hooks.json").read_text(encoding="utf-8")
    (shared_hooks.definitions_dir() / "broken.json").write_text(content, encoding="utf-8")

    with pytest.raises(shared_hooks.DefinitionError):
        shared_hooks.project_tools(["codex", "agy"])
    with pytest.raises(shared_hooks.DefinitionError):
        shared_hooks.project_claude({})
    assert (world / ".codex" / "hooks.json").read_text(encoding="utf-8") == before
    loaded, broken = shared_hooks.load_readable()
    assert loaded == [PUSH] and len(broken) == 1


def test_sharing_keeps_options_and_takes_only_the_chosen_copy(world: Path) -> None:
    from ai_config.commands.hooks import run_hooks

    chosen = {"type": "command", "command": "a", "timeout": 7, "async": True, "statusMessage": "mine"}
    twin = {"type": "command", "command": "a", "timeout": 7, "async": True, "statusMessage": "mine"}
    hooks.write_settings({"hooks": {
        "PostToolUse": [{"matcher": "Bash", "hooks": [chosen]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [twin]}],
    }})

    assert run_hooks(["share", "1", "--name", "keep"]) == 0

    settings = hooks.read_settings()
    # 別的事件裡一模一樣的那個不能跟著不見
    assert _entries(settings, "PreToolUse") == [twin]
    projected = _entries(settings, "PostToolUse")
    assert projected == [{"type": "command", "command": "a", "timeout": 7, "async": True,
                          "statusMessage": "acg：共用 keep"}]
    assert run_hooks(["unshare", "keep"]) == 0
    assert _entries(hooks.read_settings(), "PostToolUse") == [chosen]


def test_a_hook_with_fields_sharing_cannot_carry_is_refused(world: Path) -> None:
    from ai_config.commands.hooks import run_hooks

    hooks.write_settings({"hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": "a", "args": ["x"]}]}]}})

    assert run_hooks(["share", "1", "--name", "nope"]) == 1
    assert shared_hooks.load_all() == []


def test_a_failed_settings_write_leaves_no_definition(world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_config.commands import hooks as command

    hooks.write_settings({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "a"}]}]}})

    def broken(document: dict) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(command, "_save", broken)

    assert command.run_hooks(["share", "1", "--name", "half"]) == 1
    assert shared_hooks.load_all() == []
    assert [e["command"] for e in _entries(hooks.read_settings(), "PostToolUse")] == ["a"]
