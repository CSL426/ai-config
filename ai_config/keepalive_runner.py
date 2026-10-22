"""Sending the throwaway call that anchors a usage window, and logging it."""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import keepalive_settings
from .subproc import UTF8


def _append_log(message: str, tool: str = keepalive_settings.DEFAULT_TOOL) -> None:
    path = keepalive_settings.log_path(tool)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        # 寫不進日誌不該讓這次呼叫失敗:呼叫本身才是重點
        pass


def cheapest_codex_model(home: Path) -> "str | None":
    """The least promoted model this account lists, or None to leave codex's own.

    The call exists to have happened, so it should cost the least on
    offer. The cache carries no prices; the listing order is the only
    signal, and the model promoted last is the best guess at the
    cheapest. Taken from the account's own list, never named here, so a
    retired model is simply no longer picked.
    """
    try:
        cache = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    models = cache.get("models") if isinstance(cache, dict) else None
    listed = [
        model for model in models or []
        if isinstance(model, dict) and model.get("slug")
        and model.get("visibility") == "list" and model.get("supported_in_api", True)
        and isinstance(model.get("priority"), int)
    ]
    if not listed:
        return None
    return max(listed, key=lambda model: model["priority"])["slug"]


def cheapest_agy_model(listing: str) -> "str | None":
    """The weakest model `agy models` lists: a flash, at low, from the oldest line."""
    ids = [line.split("\t", 1)[0].strip() for line in listing.splitlines() if "\t" in line]
    ids = [model for model in ids if model]
    if not ids:
        return None
    flash = [model for model in ids if "flash" in model] or ids
    low = [model for model in flash if model.endswith("-low")] or flash

    def version(model: str) -> tuple:
        found = re.search(r"(\d+(?:\.\d+)*)", model)
        return tuple(int(part) for part in found.group(1).split(".")) if found else (999,)

    return min(low, key=version)


def _agy_listing() -> str:
    try:
        done = subprocess.run(
            [keepalive_settings.tool_binary("agy"), "models"], capture_output=True, text=True, **UTF8,
            timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout or ""


def codex_homes() -> list:
    """Each codex account on this machine, once: every ~/.codex* home with credentials.

    Accounts switch by CODEX_HOME inside a shell function, which a
    scheduled call never loads -- it used the default home and woke only
    the account that home links to. Homes sharing one credentials file
    are one account; the home holding the real file is kept, since that
    is the one the shell function points CODEX_HOME at.
    """
    import hashlib

    seen: dict = {}
    for home in sorted(keepalive_settings.HOME.glob(".codex*")):
        auth = home / "auth.json"
        if not home.is_dir() or not auth.is_file():
            continue
        try:
            key = hashlib.sha256(auth.read_bytes()).hexdigest()
        except OSError:
            continue
        kept = seen.get(key)
        if kept is None or ((kept / "auth.json").is_symlink() and not auth.is_symlink()):
            seen[key] = home
    return sorted(seen.values())


def send(tool: str = keepalive_settings.DEFAULT_TOOL) -> int:
    """Make the call, once per account. A failure is logged and nothing else.

    Missing one costs a window that starts later than intended, which is
    not worth waking anyone for.
    """
    keepalive_settings._check_tool(tool)
    args = keepalive_settings.run_args(keepalive_settings.load(tool), tool)
    if tool == "agy":
        return _send_agy(args, tool)
    homes = codex_homes() if tool == "codex" else []
    if not homes:
        return _call(args, tool, tool, None)
    worst = 0
    for home in homes:
        env = {**os.environ, "CODEX_HOME": str(home)}
        # 每個帳號看得到的模型不一樣,各自從自己的清單挑
        model = cheapest_codex_model(home)
        chosen = [*args[:-1], "-m", model, args[-1]] if model else args
        label = f"{tool} ({home.name})"
        _append_log(f"model {model or '(codex 預設)'}", tool)
        # 一個帳號額度用完不該讓其他帳號跳過喚醒
        worst = max(worst, _call(chosen, tool, label, env))
    return worst


def _send_agy(args: list, tool: str) -> int:
    """One conversation a month, resumed every call, run from acg's own folder.

    agy keeps every print-mode call as a conversation of the folder it ran
    in, and has no way to delete one. Three calls a day from the home
    folder left 123 "reply with only the word: hi" conversations, 46 MB, in
    the list a person resumes from (A4000, 2026-10-22). Monthly, so the
    resumed conversation never grows long.
    """
    model = cheapest_agy_model(_agy_listing())
    label = f"{tool} {model}" if model else tool
    flags = [*(["--model", model] if model else []), "--output-format", "json"]
    workdir = keepalive_settings._state_dir() / "keepalive-agy"
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        workdir = None
    month = datetime.now().astimezone().strftime("%Y-%m")
    record = _read_agy_conversation()
    resumed = record.get("id") if record.get("month") == month else None
    if resumed:
        code, result = _run(
            [args[0], *flags, "--conversation", resumed, *args[1:]], tool, label, None, workdir
        )
        if code == 0 or result is None:
            return code
        # 對話被刪了或壞了:開一個新的,不讓這次喚醒落空
        _append_log(f"could not resume conversation {resumed}; starting a new one", tool)
    code, result = _run([args[0], *flags, *args[1:]], tool, label, None, workdir)
    started = _agy_conversation_id(result)
    if code == 0 and started:
        _write_agy_conversation({"id": started, "month": month})
    return code


def _agy_conversation_path() -> Path:
    return keepalive_settings._state_dir() / "keepalive-agy-conversation.json"


def _read_agy_conversation() -> dict:
    try:
        record = json.loads(_agy_conversation_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _write_agy_conversation(record: dict) -> None:
    try:
        _agy_conversation_path().write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        # 記不住只代表下次多開一個對話,不該讓喚醒算失敗
        pass


def _agy_conversation_id(result: "subprocess.CompletedProcess | None") -> "str | None":
    document = _json_reply(result.stdout if result else "")
    found = document.get("conversation_id") if document else None
    return found if isinstance(found, str) and found else None


def _json_reply(stdout: str) -> "dict | None":
    try:
        document = json.loads(stdout or "")
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _call(args: list, tool: str, label: str, env: "dict | None") -> int:
    return _run(args, tool, label, env)[0]


def _run(
    args: list, tool: str, label: str, env: "dict | None", cwd: "Path | None" = None,
) -> "tuple[int, subprocess.CompletedProcess | None]":
    _append_log(f"calling {label}", tool)
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, **UTF8,
            timeout=120, check=False, env=env, cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _append_log(f"failed to start {label}: {exc}", tool)
        return 1, None
    reason = _outcome(result)
    _append_log(f"exit {result.returncode}: {reason}", tool)
    if result.returncode and _USAGE_LIMIT.search(reason):
        # 額度用完代表這個帳號的視窗本來就在跑,喚醒的目的已經達成
        _append_log(f"{label}: usage limit reached, its window is already running", tool)
        return 0, result
    return result.returncode, result


_ERROR_HINT = re.compile(
    r"error|fail|limit|not supported|denied|invalid", re.IGNORECASE,
)
_USAGE_LIMIT = re.compile(r"usage limit|hit your limit|limit reached", re.IGNORECASE)


def _outcome(result: "subprocess.CompletedProcess") -> str:
    """The one line worth keeping: the reply, or on failure, the reason.

    A failed call used to log only its exit code. The reason was on
    stderr the whole time -- an exhausted usage limit ran for two days
    as "exit 1: (no output)" -- and finding it meant rerunning by hand.
    """
    document = _json_reply(result.stdout)
    if document is not None and isinstance(document.get("response"), str):
        # agy 用 --output-format json 時回覆在 response 裡
        reply = document["response"].strip().splitlines()
    else:
        reply = (result.stdout or "").strip().splitlines()
    if result.returncode == 0:
        return reply[-1] if reply else "(no output)"
    lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
    # 工具在錯誤後面還會印 hook 與橫幅,最後一行常是雜訊,要找錯誤那行
    flagged = [line for line in lines if _ERROR_HINT.search(line)]
    reason = (flagged or lines or reply or ["(no output)"])[-1]
    return reason[:300]
