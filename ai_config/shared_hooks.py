"""Hooks written once, in Claude Code's format, and projected into Codex and Antigravity.

A skill in claude/skills/ reaches all three tools; a hook in Claude Code's
settings.json reached only Claude Code, so a "whenever I push" rule
silently skipped the other two. A shared hook lives as a definition in
the data repository and apply writes it into each tool's own hooks file.

Codex takes Claude Code's format as it is. Antigravity groups its hooks
under named entries, sends a different payload without the tool's
output, and can only add context before the model's next step; its
entries go through `__agy-hook`, which adapts both directions. See
docs/shared-hooks-spec.md.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import memory_paths
from .hooks import PREFIX, _strip

MARKER = f"{PREFIX}共用 "
TARGETS = ("both", "codex", "agy")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# command/timeout/statusMessage 另外存;這些選項照原樣帶著走,其他的不接受
CARRIED_OPTIONS = frozenset({"async", "once", "if", "shell"})

CODEX_EVENTS = frozenset({
    "PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
    "SessionStart", "SessionEnd", "UserPromptSubmit", "SubagentStart", "SubagentStop",
})
# Antigravity 擋工具的回應格式沒有文件,PreToolUse 投過去會擋不住,所以不接
AGY_EVENTS = frozenset({"PostToolUse", "SessionStart"})
AGY_TOOL_EVENTS = frozenset({"PostToolUse"})
# Claude Code 的工具名 → Antigravity 的;只轉接 shell,其他工具的 payload 沒有對應
AGY_TOOLS = {"Bash": "run_command"}
AGY_PREFIX = "acg-"
AGY_INJECT = "acg-inject"
_RESERVED = frozenset({"inject"})
_ADAPTER = "__agy-hook"
_AGY_ADAPTER_SECONDS = 10


class DefinitionError(ValueError):
    """A definition in the data repository that cannot be projected as written."""


@dataclass(frozen=True)
class SharedHook:
    name: str
    event: str
    command: str
    matcher: str = ""
    timeout: int = 60
    to: str = "both"
    # 原本的 statusMessage(unshare 時還原)與 async、once 這類選項
    label: str = ""
    options: dict = field(default_factory=dict, compare=False, hash=False)

    def reaches(self, tool: str) -> bool:
        return tool == "claude" or self.to in ("both", tool)

    def agy_matcher(self) -> "str | None":
        parts = self.matcher.split("|") if self.matcher else []
        if not parts or not all(part in AGY_TOOLS for part in parts):
            return None
        return "|".join(AGY_TOOLS[part] for part in parts)

    def unsupported(self, tool: str) -> str:
        """Why this hook does not reach that tool; empty when it does."""
        if not self.reaches(tool):
            return "沒有分享給它"
        if tool == "codex" and self.event not in CODEX_EVENTS:
            return f"Codex 沒有 {self.event} 事件"
        if tool == "agy":
            if self.event not in AGY_EVENTS:
                return f"Antigravity 這裡只接 {', '.join(sorted(AGY_EVENTS))}"
            if self.event in AGY_TOOL_EVENTS and self.agy_matcher() is None:
                return "Antigravity 只轉接 matcher 為 Bash 的 hook"
            if self.options:
                return f"Antigravity 不支援 {', '.join(sorted(self.options))}"
        return ""


# ─── definitions in the data repository ──────────────────────

def definitions_dir() -> Path:
    from . import paths

    return paths.SCRIPT_DIR / "claude" / "shared-hooks"


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name)) and name not in _RESERVED


def _parse(path: Path) -> SharedHook:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise DefinitionError(f"共用 hook 定義讀不了:{path}({exc})") from exc
    if not isinstance(data, dict):
        raise DefinitionError(f"共用 hook 定義不是物件:{path}")
    event, command = data.get("event"), data.get("command")
    if not isinstance(event, str) or not event or not isinstance(command, str) or not command:
        raise DefinitionError(f"共用 hook 定義缺 event 或 command:{path}")
    timeout = data.get("timeout", 60)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise DefinitionError(f"共用 hook 定義的 timeout 不是正整數:{path}")
    to = data.get("to", "both")
    if to not in TARGETS:
        # 默默當成 both 會投到使用者沒選的工具
        raise DefinitionError(f"共用 hook 定義的 to 只能是 {'|'.join(TARGETS)}:{path}")
    options = data.get("options", {})
    if not isinstance(options, dict) or set(options) - CARRIED_OPTIONS:
        allowed = ", ".join(sorted(CARRIED_OPTIONS))
        raise DefinitionError(f"共用 hook 定義的 options 只能有 {allowed}:{path}")
    return SharedHook(
        name=path.stem, event=event, command=command,
        matcher=str(data.get("matcher") or ""), timeout=timeout, to=to,
        label=str(data.get("label") or ""), options=options,
    )


def _definition_paths() -> list[Path]:
    root = definitions_dir()
    if not root.is_dir():
        return []
    return [
        path for path in sorted(root.glob("*.json"))
        if valid_name(path.stem) and not path.is_symlink()
    ]


def load_all() -> list[SharedHook]:
    """Every definition; a broken one stops projection rather than unprojecting it.

    Skipping it would strip the copies already in each tool and project
    the rest, removing that hook everywhere without a word.
    """
    return [_parse(path) for path in _definition_paths()]


def load_readable() -> "tuple[list[SharedHook], list[str]]":
    """For listings: what loads, and why each of the others does not."""
    found, broken = [], []
    for path in _definition_paths():
        try:
            found.append(_parse(path))
        except DefinitionError as exc:
            broken.append(str(exc))
    return found, broken


def definition_path(name: str) -> Path:
    if not valid_name(name):
        reserved = ", ".join(sorted(_RESERVED))
        raise ValueError(f"名稱只能用小寫英數與 . _ -,且不能是 {reserved}:{name}")
    path = definitions_dir() / f"{name}.json"
    memory_paths.assert_plain_path(path, directory=False)
    return path


def write_definition(hook: SharedHook) -> Path:
    path = definition_path(hook.name)
    data: dict = {"event": hook.event, "command": hook.command, "matcher": hook.matcher,
                  "timeout": hook.timeout, "to": hook.to}
    if hook.label:
        data["label"] = hook.label
    if hook.options:
        data["options"] = hook.options
    path.parent.mkdir(parents=True, exist_ok=True)
    memory_paths._write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return path


def delete_definition(name: str) -> bool:
    path = definition_path(name)
    if not path.is_file():
        return False
    path.unlink()
    return True


# ─── Claude Code ─────────────────────────────────────────────

def _is_shared(entry: object, name: str = "") -> bool:
    if not isinstance(entry, dict):
        return False
    message = str(entry.get("statusMessage", ""))
    return message == f"{MARKER}{name}" if name else message.startswith(MARKER)


def handler(hook: SharedHook) -> dict:
    return {
        "type": "command", "command": hook.command, "timeout": hook.timeout,
        **hook.options, "statusMessage": f"{MARKER}{hook.name}",
    }


def plain_handler(hook: SharedHook) -> dict:
    """The hook as it was before it was shared, for unshare."""
    entry = {"type": "command", "command": hook.command, "timeout": hook.timeout, **hook.options}
    if hook.label:
        entry["statusMessage"] = hook.label
    return entry


def _row(entry: dict, matcher: str) -> dict:
    row: dict = {"hooks": [entry]}
    if matcher:
        row["matcher"] = matcher
    return row


def _with_hooks(document: dict, hooks: list[SharedHook], tool: str) -> dict:
    """Claude Code's hooks shape, this tool's shared entries replaced by the current set.

    A plain copy of a shared hook goes too. Sharing removes it from this
    machine, but a copy already pushed into the database's settings.json
    came back on the next apply beside the projected one, and every push
    was reported twice.
    """
    reached = [hook for hook in hooks if not hook.unsupported(tool)]
    result = _strip(document, lambda entry: not _is_shared(entry))
    for event, rows in list((result.get("hooks") or {}).items()):
        twins = {hook.command for hook in reached if hook.event == event}
        for row in rows if isinstance(rows, list) and twins else []:
            if not isinstance(row, dict) or not isinstance(row.get("hooks"), list):
                continue
            matcher = str(row.get("matcher") or "")
            row["hooks"] = [
                entry for entry in row["hooks"]
                if not (isinstance(entry, dict) and entry.get("command") in twins
                        and any(h.command == entry.get("command") and h.matcher == matcher
                                for h in reached if h.event == event))
            ]
        if isinstance(rows, list):
            result["hooks"][event] = [
                row for row in rows if not (isinstance(row, dict) and row.get("hooks") == [])
            ]
            if not result["hooks"][event]:
                del result["hooks"][event]
    for hook in reached:
        events = result.setdefault("hooks", {})
        if not isinstance(events, dict):
            raise ValueError("hooks 必須是物件")  # noqa: TRY004
        rows = events.setdefault(hook.event, [])
        if not isinstance(rows, list):
            raise ValueError(f"{hook.event} hooks 必須是陣列")  # noqa: TRY004
        rows.append(_row(handler(hook), hook.matcher))
    return result


def project_claude(document: dict) -> dict:
    return _with_hooks(document, load_all(), "claude")


def user_hooks(document: dict) -> list[tuple[str, int, int, dict, dict]]:
    """Claude Code's own command hooks: (event, row index, entry index, row, entry)."""
    found = []
    events = document.get("hooks")
    for event, rows in (events.items() if isinstance(events, dict) else []):
        for row_index, row in enumerate(rows if isinstance(rows, list) else []):
            entries = row.get("hooks") if isinstance(row, dict) else None
            for entry_index, entry in enumerate(entries if isinstance(entries, list) else []):
                if not isinstance(entry, dict) or entry.get("type") != "command":
                    continue
                if str(entry.get("statusMessage", "")).startswith(PREFIX):
                    continue
                found.append((event, row_index, entry_index, row, entry))
    return found


def without_position(document: dict, event: str, row_index: int, entry_index: int) -> dict:
    """The document without that one entry; equal entries elsewhere stay."""
    result = json.loads(json.dumps(document))
    rows = result["hooks"][event]
    entries = rows[row_index]["hooks"]
    del entries[entry_index]
    if not entries:
        del rows[row_index]
    if not rows:
        del result["hooks"][event]
    if not result["hooks"]:
        del result["hooks"]
    return result


def with_plain(document: dict, hook: SharedHook) -> dict:
    """The document with this hook back as Claude Code's own, its projected copy gone."""
    result = _strip(document, lambda entry: not _is_shared(entry, hook.name))
    events = result.setdefault("hooks", {})
    events.setdefault(hook.event, []).append(_row(plain_handler(hook), hook.matcher))
    return result


# ─── Codex ───────────────────────────────────────────────────

def codex_homes() -> list[Path]:
    from .remember_hosts import codex_homes as homes

    return homes()


def codex_document(existing: dict, hooks: list[SharedHook]) -> dict:
    return _with_hooks(existing, hooks, "codex")


def _read_json(path: Path) -> dict:
    memory_paths.assert_plain_path(path, directory=False)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 不是 JSON 物件,先手動處理")  # noqa: TRY004
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    memory_paths._write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _codex_plan(hooks: list[SharedHook], homes: list[Path]) -> list[tuple[Path, dict]]:
    plan = []
    for home in homes:
        path = home / "hooks.json"
        existing = _read_json(path)
        wanted = codex_document(existing, hooks)
        if wanted == existing or (not path.exists() and not wanted.get("hooks")):
            continue
        plan.append((path, wanted))
    return plan


def project_codex(homes: "list[Path] | None" = None) -> list[str]:
    plan = _codex_plan(load_all(), homes if homes is not None else codex_homes())
    for path, data in plan:
        _write_json(path, data)
    return [str(path) for path, _ in plan]


def _snake(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def codex_trusted(home: Path, hook: SharedHook) -> bool:
    """Whether Codex holds a trust record at this hook's own place in that home.

    Codex keys trust by file, event, group and handler, and stores a hash
    of the hook it reviewed. acg cannot recompute that hash, so a record
    means this position was reviewed once, not in its current form.
    """
    path = home / "hooks.json"
    try:
        rows = _read_json(path).get("hooks", {}).get(hook.event, [])
        config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    group = next((index for index, row in enumerate(rows if isinstance(rows, list) else [])
                  if isinstance(row, dict)
                  and any(_is_shared(entry, hook.name) for entry in row.get("hooks", []))), None)
    if group is None:
        return False
    state = config.get("hooks", {}).get("state", {})
    record = state.get(f"{path}:{_snake(hook.event)}:{group}:0") if isinstance(state, dict) else None
    return isinstance(record, dict) and bool(record.get("trusted_hash"))


# ─── Antigravity ─────────────────────────────────────────────

def agy_hooks_path() -> Path:
    from .remember_hosts import AGY_HOOKS

    return AGY_HOOKS


def _acg_command(*args: str) -> str:
    from .paths import scheduled_command

    # Antigravity 用 sh -c 跑;正斜線在每個平台的 shell 都認得
    words = [part.replace("\\", "/") for part in scheduled_command()] + list(args)
    return " ".join(shlex.quote(word) for word in words)


def _owned(value: object) -> bool:
    """An Antigravity entry acg wrote: it calls the adapter. Other acg- keys are not ours."""
    return _ADAPTER in json.dumps(value)


def agy_document(existing: dict, hooks: list[SharedHook]) -> dict:
    result = {
        key: value for key, value in existing.items()
        if not (key.startswith(AGY_PREFIX) and _owned(value))
    }
    reached = [hook for hook in hooks if not hook.unsupported("agy")]
    for hook in reached:
        call = {
            "type": "command", "command": _acg_command(_ADAPTER, hook.name),
            # 轉接要先啟動 acg 才輪到腳本;Windows 上光啟動就要一兩秒
            "timeout": hook.timeout + _AGY_ADAPTER_SECONDS,
        }
        if hook.event in AGY_TOOL_EVENTS:
            handlers: list = [{"matcher": hook.agy_matcher(), "hooks": [call]}]
        else:
            handlers = [call]
        result[f"{AGY_PREFIX}{hook.name}"] = {"enabled": True, hook.event: handlers}
    if reached:
        result[AGY_INJECT] = {"enabled": True, "PreInvocation": [{
            "type": "command", "command": _acg_command(_ADAPTER, "--inject"), "timeout": 10,
        }]}
    return result


def _snapshot_dir() -> Path:
    from .autoupdate import state_dir

    return state_dir() / "agy-hooks"


def _snapshots(hooks: list[SharedHook]) -> dict[Path, dict]:
    """What the adapter runs: fixed at apply, like the copies Claude Code and Codex get.

    Reading the data repository at run time would let a changed
    definition run in Antigravity before anyone applied it.
    """
    return {
        _snapshot_dir() / f"{hook.name}.json": {
            "event": hook.event, "command": hook.command, "timeout": hook.timeout,
        }
        for hook in hooks if not hook.unsupported("agy")
    }


def project_agy() -> list[str]:
    from .remember_hosts import _load_agy_hooks, _write_agy_hooks

    hooks = load_all()
    existing = _load_agy_hooks()
    wanted = agy_document(existing, hooks)
    snapshots = _snapshots(hooks)
    root = _snapshot_dir()
    stale = [path for path in root.glob("*.json") if path not in snapshots] if root.is_dir() else []
    for path, data in snapshots.items():
        if not path.is_file() or _read_json(path) != data:
            _write_json(path, data)
    for path in stale:
        path.unlink()
    if wanted == existing:
        return []
    _write_agy_hooks(wanted)
    return [str(agy_hooks_path())]


def project_tools(tools: list[str]) -> list[str]:
    """Codex and Antigravity keep hooks outside what apply mirrors; Claude's ride in settings.json.

    Every Codex home is read and checked before the first write, so one
    that cannot be read stops the projection instead of leaving it half done.
    """
    hooks = load_all()
    codex_plan = _codex_plan(hooks, codex_homes()) if "codex" in tools else []
    changed = []
    for path, data in codex_plan:
        _write_json(path, data)
        changed.append(str(path))
    if "agy" in tools:
        changed += project_agy()
    return changed


# ─── the Antigravity adapter (`__agy-hook`) ──────────────────

def _context_dir() -> Path:
    from .autoupdate import state_dir

    return state_dir() / "agy-hook-context"


def _context_file(conversation: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", conversation)[:80] or "unknown"
    return _context_dir() / f"{safe}.txt"


def claude_payload(agy: dict, event: str) -> dict:
    """Antigravity's payload in Claude Code's shape, so one script serves every tool."""
    call = agy.get("toolCall") if isinstance(agy.get("toolCall"), dict) else {}
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    paths = agy.get("workspacePaths") if isinstance(agy.get("workspacePaths"), list) else []
    tool = str(call.get("name", ""))
    names = {value: key for key, value in AGY_TOOLS.items()}
    payload: dict = {
        "hook_event_name": event,
        "session_id": str(agy.get("conversationId", "")),
        "cwd": str(args.get("Cwd") or (paths[0] if paths else "")),
        "transcript_path": str(agy.get("transcriptPath", "")),
    }
    if tool:
        payload["tool_name"] = names.get(tool, tool)
        payload["tool_input"] = (
            {"command": str(args.get("CommandLine", ""))} if tool == "run_command" else args
        )
    return payload


def _additional_context(output: str) -> str:
    try:
        data = json.loads(output)
    except ValueError:
        return ""
    specific = data.get("hookSpecificOutput") if isinstance(data, dict) else None
    text = specific.get("additionalContext") if isinstance(specific, dict) else None
    return text.strip() if isinstance(text, str) else ""


def _bash() -> "str | None":
    """Git Bash on Windows, where `bash` on PATH may be WSL's launcher instead."""
    import shutil

    if os.name == "nt":
        git = shutil.which("git")
        folder = Path(git).resolve().parent if git else None
        for _ in range(3):
            if folder is None:
                break
            candidate = folder / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
            folder = folder.parent if folder.parent != folder else None
    return shutil.which("bash")


def _shell(command: str) -> list[str]:
    bash = _bash()
    return [bash, "-c", command] if bash else ["sh", "-c", command]


def run_agy_hook(args: list[str]) -> int:
    """Antigravity calls this; it must answer JSON and never fail the turn."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        reply = _inject(payload) if args[:1] == ["--inject"] else _relay(args[:1], payload)
    except Exception:  # noqa: BLE001 — hook 壞掉不能卡住 Antigravity 的對話
        reply = {}
    print(json.dumps(reply, ensure_ascii=False))
    return 0


def _relay(names: list[str], payload: dict) -> dict:
    if not names or not valid_name(names[0]):
        return {}
    snapshot = _read_json(_snapshot_dir() / f"{names[0]}.json")
    command, event = snapshot.get("command"), snapshot.get("event")
    if not isinstance(command, str) or not isinstance(event, str):
        return {}
    timeout = snapshot.get("timeout") if isinstance(snapshot.get("timeout"), int) else 60
    converted = claude_payload(payload, event)
    # 跑的是 apply 當時定案的指令,跟 Claude Code、Codex 拿到的那份一樣
    done = subprocess.run(
        _shell(command), input=json.dumps(converted, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, cwd=converted["cwd"] or None,
    )
    text = _additional_context(done.stdout)
    if text and converted["session_id"]:
        path = _context_file(converted["session_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return {}


def _inject(payload: dict) -> dict:
    conversation = str(payload.get("conversationId", ""))
    if not conversation:
        return {}
    path = _context_file(conversation)
    # 先搬開再讀:搬開之後才追加的提醒進新檔留給下一步;兩個同時搬只有一個成功
    taken = path.with_name(f"{path.stem}.{os.getpid()}.taking")
    try:
        os.replace(path, taken)
    except OSError:
        return {}
    try:
        text = taken.read_text(encoding="utf-8").strip()
    finally:
        taken.unlink(missing_ok=True)
    return {"injectSteps": [{"ephemeralMessage": text}]} if text else {}
