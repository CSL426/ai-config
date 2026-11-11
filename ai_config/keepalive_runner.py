"""Sending the throwaway call that anchors a usage window, and logging it."""

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import keepalive_scheduler, keepalive_settings
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
# 樣式互斥:Gemini 4 的 flash 算 Gemini 4 那組;同組裡便宜的排前面(GPT-OSS、Sonnet)
_AGY_GROUPS = (
    ("gemini-flash", (r"^gemini-(?!4)[^-]*-flash",)),
    ("gemini-4", (r"^gemini-4",)),
    ("claude-gpt", (r"^gpt-oss", r"^gpt-", r"^claude-sonnet", r"^claude-")),
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


def send(tool: str = keepalive_settings.DEFAULT_TOOL, home: "str | None" = None) -> int:
    """Make the call, once per account. A failure is logged and nothing else.

    Missing one costs a window that starts later than intended, which is
    not worth waking anyone for. `home` limits a codex call to one
    account: the one-shot wake after an exhausted quota must not call
    the others, whose windows run on their own schedule.
    """
    keepalive_settings._check_tool(tool)
    if home is not None and tool != "codex":
        raise ValueError(f"--home 只適用於 codex,不適用於 {tool}")
    settings = keepalive_settings.load(tool)
    args = keepalive_settings.run_args(settings, tool)
    if tool == "agy":
        return _send_agy(args, tool)
    homes = codex_homes() if tool == "codex" else []
    if home is not None:
        named = {found.name: found for found in homes}
        if home not in named:
            raise ValueError(
                f"不認得這個 codex 帳號:{home}(可用:{', '.join(named) or '無'})"
            )
        homes = [named[home]]
    if not homes:
        return _call(args, tool, tool, None)
    worst = 0
    for found in homes:
        env = {**os.environ, "CODEX_HOME": str(found)}
        # 每個帳號看得到的模型不一樣,各自從自己的清單挑
        model = cheapest_codex_model(found)
        chosen = [*args[:-1], "-m", model, args[-1]] if model else args
        label = f"{tool} ({found.name})"
        _append_log(f"model {model or '(codex 預設)'}", tool)
        before = _codex_snapshot(found, tool)
        started = time.time()
        code, result = _run(chosen, tool, label, env)
        _after_codex_call(found, settings.prompt, before, started, code, result, tool)
        # 一個帳號額度用完不該讓其他帳號跳過喚醒
        worst = max(worst, code)
    return worst


def _codex_snapshot(home: Path, tool: str) -> "frozenset | None":
    from . import keepalive_limits

    try:
        return keepalive_limits.snapshot(home)
    except (OSError, ValueError) as exc:
        _append_log(f"{home.name}: could not list its rollouts: {exc}", tool)
        return None


def _after_codex_call(
    home: Path, prompt: str, before: "frozenset | None", started: float, code: int,
    result: "subprocess.CompletedProcess | None", tool: str,
) -> None:
    """Keep what a successful call reported; plan a wake after an exhausted one.

    Nothing here may fail the keepalive itself: the call already happened.
    """
    if result is None:
        return
    if result.returncode == 0:
        steps = [
            lambda: _remember_codex_limits(home, prompt, before, started, tool),
            # 記不下額度不該讓已經醒來的帳號還留著一個待執行的喚醒
            lambda: _log_if(keepalive_scheduler.cancel_wake(home.name), tool),
        ]
    elif code == 0:
        # _run 把額度用完當成功回傳;這種失敗的 rate_limits 是 null,重置時間只能看上次的紀錄
        steps = [lambda: _plan_codex_wake(home, tool)]
    else:
        steps = []
    for step in steps:
        try:
            step()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            _append_log(f"{home.name}: could not handle its quota record: {exc}", tool)


def _log_if(line: str, tool: str) -> None:
    if line:
        _append_log(line, tool)


def _remember_codex_limits(
    home: Path, prompt: str, before: "frozenset | None", started: float, tool: str,
) -> None:
    from . import keepalive_limits

    if before is None:
        return
    rollout, reason = keepalive_limits.find_rollout(home, prompt, before, started)
    if rollout is None:
        _append_log(f"{home.name}: limits not recorded: {reason}", tool)
        return
    windows = keepalive_limits.read_limits(rollout)
    if not windows:
        _append_log(f"{home.name}: limits not recorded: {rollout.name} reports no codex limits", tool)
        return
    keepalive_limits.remember(home.name, windows)


def _plan_codex_wake(home: Path, tool: str) -> None:
    from . import keepalive_limits

    if not keepalive_scheduler.installed("codex"):
        # 單獨喚醒是排程的一部分:沒啟用排程時手動 send 不該留下一個排程
        _append_log(f"{home.name}: codex keepalive is not enabled; no one-shot wake", tool)
        return
    record = keepalive_limits.read_json(
        keepalive_limits.limits_path(), lambda line: _append_log(line, tool),
    ).get(home.name)
    now = datetime.now(UTC)
    slot = keepalive_limits.next_slot(keepalive_settings.load("codex").times, now)
    wake, reason = keepalive_limits.decide(record, now, slot)
    if wake is None:
        _append_log(f"{home.name}: no one-shot wake: {reason}", tool)
        return
    _, line = keepalive_scheduler.schedule_wake(home.name, wake)
    _append_log(f"{home.name}: {line}", tool)


def _send_agy(args: list, tool: str) -> int:
    """One conversation a month, resumed every call, run from acg's own folder.

    agy keeps every print-mode call as a conversation of the folder it ran
    in, and has no way to delete one. Three calls a day from the home
    folder left 123 "reply with only the word: hi" conversations, 46 MB, in
    the list a person resumes from (2026-10-22). Monthly, so the
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
    month = _this_month()
    record = _read_agy_conversation()
    known = record.get("groups") if record.get("month") == month else None
    conversations = dict(known) if isinstance(known, dict) else {}
    resting = record.get("resting") if isinstance(record.get("resting"), dict) else {}
    now = datetime.now().astimezone()
    listing = _agy_listing()
    picks = agy_models_per_group(listing)
    if not picks:
        # 讀不到清單或一個都認不得時,只能讓 agy 用它的預設模型點一組;要留下原因
        _append_log("agy models gave no recognised model; calling agy's default only", tool)
        picks = [("default", None)]
    worst = 0
    for group, model in picks:
        until = _parse_time(resting.get(group))
        if until and until > now:
            # 額度用完的組在重置前打了也沒用,還會讓 agy 多留一個空對話
            _append_log(f"{group}: quota exhausted until {until:%m-%d %H:%M}; skipped", tool)
            continue
        # 一組失敗不該讓另一組跳過喚醒
        code, started, rests_until = _wake_agy_group(
            args, tool, model, conversations.get(group), workdir
        )
        # 被訊號終止時回傳碼是負的,也要算失敗
        worst = max(worst, abs(code))
        if started:
            conversations[group] = started
        if rests_until:
            resting[group] = rests_until.isoformat()
        else:
            resting.pop(group, None)
        if started or rests_until or group in record.get("resting", {}):
            _write_agy_conversation({"month": month, "groups": conversations, "resting": resting})
    return worst


def _this_month() -> str:
    return datetime.now().astimezone().strftime("%Y-%m")


def _parse_time(value: object) -> "datetime | None":
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _quota_rest(result: "subprocess.CompletedProcess | None") -> "datetime | None":
    """When an exhausted quota resets, from agy's "Resets in 99h36m"; None if it is not one."""
    if result is None or result.returncode == 0:
        return None
    text = f"{result.stderr or ''}\n{result.stdout or ''}"
    if not _USAGE_LIMIT.search(text):
        return None
    found = _RESETS_IN.search(text)
    hours = int(found.group(1) or 0) if found else 0
    minutes = int(found.group(2) or 0) if found else 0
    # 說不出何時重置就先停五小時,跟一個視窗一樣長
    wait = timedelta(hours=hours, minutes=minutes) if (hours or minutes) else timedelta(hours=5)
    return datetime.now().astimezone() + wait


def _wake_agy_group(
    args: list, tool: str, model: "str | None", resumed: object, workdir: "Path | None",
) -> "tuple[int, str | None, datetime | None]":
    """One call for one quota group, in that group's conversation for the month."""
    label = f"{tool} {model}" if model else tool
    flags = [*(["--model", model] if model else []), "--output-format", "json"]
    if isinstance(resumed, str) and resumed:
        code, result = _run(
            [args[0], *flags, "--conversation", resumed, *args[1:]], tool, label, None, workdir,
            json_reply=True,
        )
        if code == 0 or result is None:
            return code, None, _quota_rest(result)
        # 對話被刪了或壞了:開一個新的,不讓這次喚醒落空。agy 的錯誤訊息分不出
        # 「找不到對話」和其他失敗,所以一律換新,最壞是多一個對話
        _append_log(f"could not resume conversation {resumed}; starting a new one", tool)
    code, result = _run(
        [args[0], *flags, *args[1:]], tool, label, None, workdir, json_reply=True
    )
    started = _agy_conversation_id(result)
    return code, started if code == 0 else None, _quota_rest(result)


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
# agy 額度用完時說的是 429 RESOURCE_EXHAUSTED「Individual quota reached」;沒認出來的話,
# 接續對話失敗會被當成對話壞了而改開新的,每次排程都多留一個空對話(2026-11-01)
_USAGE_LIMIT = re.compile(
    r"usage limit|hit your limit|limit reached|quota reached", re.IGNORECASE,
)
_RESETS_IN = re.compile(r"Resets in (?:(\d+)h)?(?:(\d+)m)?", re.IGNORECASE)


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
