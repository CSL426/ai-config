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
from dataclasses import asdict, dataclass
from pathlib import Path

from . import memory_paths
from .hooks import PREFIX, _strip

MARKER = f"{PREFIX}共用 "
TARGETS = ("both", "codex", "agy")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

CODEX_EVENTS = frozenset({
    "PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
    "SessionStart", "SessionEnd", "UserPromptSubmit", "SubagentStart", "SubagentStop",
})
AGY_TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse"})
AGY_EVENTS = AGY_TOOL_EVENTS | {"SessionStart"}
# Claude Code 的工具名 → Antigravity 的;沒有對應的就不投影,免得 matcher 對不到東西
AGY_TOOLS = {"Bash": "run_command"}
AGY_PREFIX = "acg-"
AGY_INJECT = "acg-inject"
_AGY_ADAPTER_SECONDS = 10


@dataclass(frozen=True)
class SharedHook:
    name: str
    event: str
    command: str
    matcher: str = ""
    timeout: int = 60
    to: str = "both"

    def reaches(self, tool: str) -> bool:
        return tool == "claude" or self.to in ("both", tool)

    def agy_matcher(self) -> "str | None":
        if self.matcher in ("", "*"):
            return "*"
        parts = self.matcher.split("|")
        if not all(part in AGY_TOOLS for part in parts):
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
                return f"Antigravity 沒有對應 {self.matcher} 的工具"
        return ""


# ─── definitions in the data repository ──────────────────────

def definitions_dir() -> Path:
    from . import paths

    return paths.SCRIPT_DIR / "claude" / "shared-hooks"


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name))


def _parse(path: Path) -> "SharedHook | None":
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    event, command = data.get("event"), data.get("command")
    if not isinstance(event, str) or not isinstance(command, str) or not command:
        return None
    timeout = data.get("timeout", 60)
    to = data.get("to", "both")
    return SharedHook(
        name=path.stem, event=event, command=command,
        matcher=str(data.get("matcher") or ""),
        timeout=timeout if isinstance(timeout, int) and timeout > 0 else 60,
        to=to if to in TARGETS else "both",
    )


def load_all() -> list[SharedHook]:
    root = definitions_dir()
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob("*.json")):
        if not valid_name(path.stem) or path.is_symlink():
            continue
        hook = _parse(path)
        if hook is not None:
            found.append(hook)
    return found


def write_definition(hook: SharedHook) -> Path:
    if not valid_name(hook.name):
        raise ValueError(f"名稱只能用小寫英數與 . _ -:{hook.name}")
    path = definitions_dir() / f"{hook.name}.json"
    memory_paths.assert_plain_path(path, directory=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {key: value for key, value in asdict(hook).items() if key != "name"}
    memory_paths._write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return path


def delete_definition(name: str) -> bool:
    path = definitions_dir() / f"{name}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True


# ─── Claude Code ─────────────────────────────────────────────

def _is_shared(entry: object) -> bool:
    return isinstance(entry, dict) and str(entry.get("statusMessage", "")).startswith(MARKER)


def handler(hook: SharedHook) -> dict:
    return {
        "type": "command", "command": hook.command, "timeout": hook.timeout,
        "statusMessage": f"{MARKER}{hook.name}",
    }


def _row(hook: SharedHook) -> dict:
    row: dict = {"hooks": [handler(hook)]}
    if hook.matcher:
        row["matcher"] = hook.matcher
    return row


def _with_hooks(document: dict, hooks: list[SharedHook], tool: str) -> dict:
    """Claude Code's hooks shape, this tool's shared entries replaced by the current set."""
    result = _strip(document, lambda entry: not _is_shared(entry))
    for hook in hooks:
        if hook.unsupported(tool):
            continue
        events = result.setdefault("hooks", {})
        if not isinstance(events, dict):
            raise ValueError("hooks 必須是物件")  # noqa: TRY004
        rows = events.setdefault(hook.event, [])
        if not isinstance(rows, list):
            raise ValueError(f"{hook.event} hooks 必須是陣列")  # noqa: TRY004
        rows.append(_row(hook))
    return result


def project_claude(document: dict) -> dict:
    return _with_hooks(document, load_all(), "claude")


def user_hooks(document: dict) -> list[tuple[str, dict, dict]]:
    """Claude Code's own hooks, the ones nobody projected: (event, row, entry)."""
    found = []
    events = document.get("hooks")
    for event, rows in (events.items() if isinstance(events, dict) else []):
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            for entry in row.get("hooks", []) if isinstance(row.get("hooks"), list) else []:
                if not isinstance(entry, dict) or entry.get("type") != "command":
                    continue
                if str(entry.get("statusMessage", "")).startswith(PREFIX):
                    continue
                found.append((event, row, entry))
    return found


def without_entry(document: dict, target: dict) -> dict:
    return _strip(document, lambda entry: entry is not target and entry != target)


# ─── Codex ───────────────────────────────────────────────────

def codex_homes() -> list[Path]:
    from .remember_hosts import codex_homes as homes

    return homes()


def codex_document(existing: dict, hooks: list[SharedHook]) -> dict:
    return _with_hooks(existing, hooks, "codex")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 不是 JSON 物件,先手動處理")  # noqa: TRY004
    return data


def project_codex(homes: "list[Path] | None" = None) -> list[str]:
    hooks = load_all()
    changed = []
    for home in homes if homes is not None else codex_homes():
        path = home / "hooks.json"
        memory_paths.assert_plain_path(path, directory=False)
        existing = _read_json(path)
        wanted = codex_document(existing, hooks)
        if wanted == existing or (not path.exists() and not wanted.get("hooks")):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        memory_paths._write_text_atomic(path, json.dumps(wanted, ensure_ascii=False, indent=2) + "\n")
        changed.append(str(path))
    return changed


def _snake(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def codex_trusted(home: Path, hook: SharedHook) -> bool:
    """Whether this home's config holds any trust record for the event's hooks.

    Codex hashes the hook it reviewed; acg cannot recompute that, so a
    record means "reviewed once", not "reviewed in this exact form".
    """
    try:
        config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    state = config.get("hooks", {}).get("state", {})
    prefix = f"{home / 'hooks.json'}:{_snake(hook.event)}:"
    return any(
        key.startswith(prefix) and isinstance(value, dict) and value.get("trusted_hash")
        for key, value in (state.items() if isinstance(state, dict) else [])
    )


# ─── Antigravity ─────────────────────────────────────────────

def agy_hooks_path() -> Path:
    from .remember_hosts import AGY_HOOKS

    return AGY_HOOKS


def _acg_command(*args: str) -> str:
    from .paths import scheduled_command

    # Antigravity 用 sh -c 跑;正斜線在每個平台的 shell 都認得
    words = [part.replace("\\", "/") for part in scheduled_command()] + list(args)
    return " ".join(shlex.quote(word) for word in words)


def agy_document(existing: dict, hooks: list[SharedHook]) -> dict:
    result = {key: value for key, value in existing.items() if not key.startswith(AGY_PREFIX)}
    reached = [hook for hook in hooks if not hook.unsupported("agy")]
    for hook in reached:
        call = {
            "type": "command", "command": _acg_command("__agy-hook", hook.name),
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
            "type": "command", "command": _acg_command("__agy-hook", "--inject"), "timeout": 10,
        }]}
    return result


def project_agy() -> list[str]:
    from .remember_hosts import _load_agy_hooks, _write_agy_hooks

    existing = _load_agy_hooks()
    wanted = agy_document(existing, load_all())
    if wanted == existing:
        return []
    _write_agy_hooks(wanted)
    return [str(agy_hooks_path())]


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
    hook = next((h for h in load_all() if names and h.name == names[0]), None)
    if hook is None:
        return {}
    converted = claude_payload(payload, hook.event)
    done = subprocess.run(
        _shell(hook.command), input=json.dumps(converted, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=hook.timeout, check=False, cwd=converted["cwd"] or None,
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
    try:
        text = path.read_text(encoding="utf-8").strip()
        path.unlink()
    except OSError:
        return {}
    return {"injectSteps": [{"ephemeralMessage": text}]} if text else {}


def project_all() -> list[str]:
    """Write every tool's projection on this machine; what changed, for the log."""
    changed = project_codex()
    changed += project_agy()
    return changed
