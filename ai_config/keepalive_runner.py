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


# agy 的額度按模型分組,每組各有自己的五小時視窗。使用者 2026-10-30 確認:Gemini Flash
# 一組(平常在用)、Gemini 4 一組、Claude 與 GPT 共用一組;以前只打 Flash,另外兩組的
# 重置時間都沒被錨定。每組的樣式依序比對,前面的樣式優先(GPT-OSS 比 Claude 便宜)
_AGY_GROUPS = (
    ("gemini-flash", (r"^gemini-.*flash",)),
    ("gemini-4", (r"^gemini-4",)),
    ("claude-gpt", (r"^gpt-", r"^claude-")),
)
_EFFORT = ("low", "medium", "high")


def agy_models_per_group(listing: str) -> "list[tuple[str, str]]":
    """One model to call per quota group: the cheapest `agy models` lists for it.

    Within a group the earlier pattern wins (GPT-OSS before Claude), then the
    lowest effort, then the oldest version. A group the listing lacks is
    skipped; an empty listing leaves agy's own default to a single call.
    """
    ids = [line.split("\t", 1)[0].strip() for line in listing.splitlines() if "\t" in line]
    ids = [model for model in ids if model]

    def version(model: str) -> tuple:
        found = re.search(r"(\d+(?:\.\d+)*)", model)
        return tuple(int(part) for part in found.group(1).split(".")) if found else (999,)

    def effort(model: str) -> int:
        suffix = model.rsplit("-", 1)[-1]
        return _EFFORT.index(suffix) if suffix in _EFFORT else len(_EFFORT)

    def rank(model: str, patterns: tuple) -> "int | None":
        return next((i for i, pattern in enumerate(patterns) if re.search(pattern, model)), None)

    picks = []
    for group, patterns in _AGY_GROUPS:
        members = [model for model in ids if rank(model, patterns) is not None]
        if members:
            picks.append((group, min(members, key=lambda model: (
                rank(model, patterns), effort(model), version(model),
            ))))
    return picks


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
    from .locking import exclusive_lock

    # 手動 send 碰上排程時,兩邊都會讀到「還沒有對話」而各開一個
    with exclusive_lock(".acg-keepalive-agy.lock") as acquired:
        if not acquired:
            _append_log("another agy keepalive is running; it anchors the window", tool)
            return 0
        return _send_agy_locked(args, tool)


def _send_agy_locked(args: list, tool: str) -> int:
    workdir = keepalive_settings._state_dir() / "keepalive-agy"
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        workdir = None
    month = datetime.now().astimezone().strftime("%Y-%m")
    record = _read_agy_conversation()
    known = record.get("groups") if record.get("month") == month else None
    conversations = dict(known) if isinstance(known, dict) else {}
    picks = agy_models_per_group(_agy_listing()) or [("default", None)]
    worst = 0
    for group, model in picks:
        # 一組失敗不該讓另一組跳過喚醒
        code, started = _wake_agy_group(args, tool, model, conversations.get(group), workdir)
        worst = max(worst, code)
        if started:
            conversations[group] = started
            _write_agy_conversation({"month": month, "groups": conversations})
    return worst


def _wake_agy_group(
    args: list, tool: str, model: "str | None", resumed: object, workdir: "Path | None",
) -> "tuple[int, str | None]":
    """One call for one quota group, in that group's conversation for the month."""
    label = f"{tool} {model}" if model else tool
    flags = [*(["--model", model] if model else []), "--output-format", "json"]
    if isinstance(resumed, str) and resumed:
        code, result = _run(
            [args[0], *flags, "--conversation", resumed, *args[1:]], tool, label, None, workdir,
            json_reply=True,
        )
        if code == 0 or result is None:
            return code, None
        # 對話被刪了或壞了:開一個新的,不讓這次喚醒落空。agy 的錯誤訊息分不出
        # 「找不到對話」和其他失敗,所以一律換新,最壞是多一個對話
        _append_log(f"could not resume conversation {resumed}; starting a new one", tool)
    code, result = _run(
        [args[0], *flags, *args[1:]], tool, label, None, workdir, json_reply=True
    )
    started = _agy_conversation_id(result)
    return code, started if code == 0 else None


def _agy_conversation_path() -> Path:
    return keepalive_settings._state_dir() / "keepalive-agy-conversation.json"


def _read_agy_conversation() -> dict:
    try:
        record = json.loads(_agy_conversation_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _write_agy_conversation(record: dict) -> None:
    path = _agy_conversation_path()
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        # 先寫暫存檔再整檔替換:寫到一半中斷時,舊紀錄還在
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        # 記不住只代表下次多開一個對話,不該讓喚醒算失敗;但要留下原因
        _append_log(f"could not record the agy conversation: {exc}", "agy")


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
    *, json_reply: bool = False,
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
    reason = _outcome(result, json_reply=json_reply)
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


def _outcome(result: "subprocess.CompletedProcess", *, json_reply: bool = False) -> str:
    """The one line worth keeping: the reply, or on failure, the reason.

    A failed call used to log only its exit code. The reason was on
    stderr the whole time -- an exhausted usage limit ran for two days
    as "exit 1: (no output)" -- and finding it meant rerunning by hand.
    """
    document = _json_reply(result.stdout) if json_reply else None
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
