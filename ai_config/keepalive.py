"""Anchor Claude's usage window by calling it at chosen times.

The window is five hours long and starts at the account's first call of
the day, so where that call lands decides where every boundary lands
after it. Sending one throwaway prompt at a chosen hour puts the
boundaries where the day needs them.

Nothing here does work on the user's behalf: the call exists so that it
happened, and the only output is a line in a log.

Settings stay on this machine rather than in the notebook. The times a
machine wants depend on the hours someone keeps in front of it, and a
log of what ran is about this machine alone -- syncing either would have
one machine's answer overwrite another's.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import HOME

DEFAULT_TIMES = ("07:00", "12:05", "17:10", "22:15")
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_PROMPT = "reply with only the word: hi"
MAX_TIMES = 8
DEFAULT_TOOL = "claude"
TOOLS = ("claude", "codex", "agy")

# 每個工具都有自己的五小時視窗,而且互不相干:codex 的 config.toml 就把
# five-hour-limit 印在狀態列上。三邊各排各的時間,不共用一份清單
_COMMANDS = {
    "claude": ("claude", ("--model", "{model}", "-p", "{prompt}")),
    # exec 是非互動形式;reasoning effort 走 -c,那是 config.toml 的同一個鍵
    "codex": ("codex", (
        "exec", "--skip-git-repo-check",
        "-c", "model_reasoning_effort=minimal", "{prompt}",
    )),
    "agy": ("agy", ("--effort", "low", "-p", "{prompt}")),
}
_UNIT = "acg-keepalive"
_LABEL = "com.csl426.acg.keepalive"
_TASK = "acg keepalive"


def _check_tool(tool: str) -> str:
    if tool not in TOOLS:
        raise ValueError(f"不認得這個工具:{tool}(可用:{', '.join(TOOLS)})")
    return tool


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(HOME / ".local" / "state")
    return Path(base) / "acg"


def config_path(tool: str = DEFAULT_TOOL) -> Path:
    # claude 留用原本的檔名,先前啟用的機器不必重設
    name = "keepalive.json" if tool == DEFAULT_TOOL else f"keepalive-{tool}.json"
    return _state_dir() / name


def log_path(tool: str = DEFAULT_TOOL) -> Path:
    name = "keepalive.log" if tool == DEFAULT_TOOL else f"keepalive-{tool}.log"
    return _state_dir() / name


@dataclass
class Settings:
    times: tuple = DEFAULT_TIMES
    model: str = DEFAULT_MODEL
    prompt: str = DEFAULT_PROMPT
    claude_path: str = ""


@dataclass
class Parsed:
    times: list = field(default_factory=list)
    rejected: list = field(default_factory=list)


def parse_times(values) -> Parsed:
    """Accept HH:MM, in order, without duplicates.

    A rejected entry is reported rather than dropped: silently scheduling
    three of the four times somebody asked for is worse than refusing.
    """
    result = Parsed()
    for raw in values:
        text = str(raw).strip()
        hour, _, minute = text.partition(":")
        try:
            at = (int(hour), int(minute))
        except ValueError:
            result.rejected.append(text)
            continue
        if not (0 <= at[0] <= 23 and 0 <= at[1] <= 59) or len(minute) != 2:
            result.rejected.append(text)
            continue
        formatted = f"{at[0]:02d}:{at[1]:02d}"
        if formatted not in result.times:
            result.times.append(formatted)
    result.times.sort()
    return result


def load(tool: str = DEFAULT_TOOL) -> Settings:
    """This machine's settings; the defaults when it has none or they are broken."""
    _check_tool(tool)
    try:
        raw = json.loads(config_path(tool).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    parsed = parse_times(raw.get("times") or ())
    model = raw.get("model")
    prompt = raw.get("prompt")
    claude_path = raw.get("claude_path")
    return Settings(
        times=tuple(parsed.times) or DEFAULT_TIMES,
        model=model if isinstance(model, str) and model else DEFAULT_MODEL,
        prompt=prompt if isinstance(prompt, str) and prompt else DEFAULT_PROMPT,
        claude_path=claude_path if isinstance(claude_path, str) else "",
    )


def save(settings: Settings, tool: str = DEFAULT_TOOL) -> None:
    _check_tool(tool)
    path = config_path(tool)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "times": list(settings.times),
                "model": settings.model,
                "prompt": settings.prompt,
                "claude_path": settings.claude_path,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def tool_binary(tool: str = DEFAULT_TOOL, configured: str = "") -> str:
    """The launcher that survives an upgrade, not one version's own file.

    These CLIs install each version in its own directory behind a stable
    launcher. Recording the version directory works until the next update
    moves it, and the schedule then calls a path that is no longer there.
    """
    name = _COMMANDS[_check_tool(tool)][0]
    if configured:
        return configured
    candidates = [
        HOME / ".local" / "bin" / name,
        HOME / f".{name}" / "local" / name,
        Path(f"/usr/local/bin/{name}"),
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    from shutil import which

    return which(name) or name


def run_args(settings: "Settings | None" = None, tool: str = DEFAULT_TOOL) -> list:
    _check_tool(tool)
    active = settings or load(tool)
    _, template = _COMMANDS[tool]
    filled = [
        part.replace("{model}", active.model).replace("{prompt}", active.prompt)
        for part in template
    ]
    return [tool_binary(tool, active.claude_path), *filled]


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def _invocation(tool: str = DEFAULT_TOOL) -> list:
    from .paths import SCRIPT_DIR

    binary = SCRIPT_DIR / ("ai-config.exe" if os.name == "nt" else "ai-config")
    suffix = [] if tool == DEFAULT_TOOL else [tool]
    if getattr(sys, "frozen", False) and binary.is_file():
        return [str(binary), "keepalive", "send", *suffix]
    return [sys.executable, "-m", "ai_config", "keepalive", "send", *suffix]


def unit_name(tool: str = DEFAULT_TOOL) -> str:
    return _UNIT if tool == DEFAULT_TOOL else f"{_UNIT}-{tool}"


def systemd_units(times, tool: str = DEFAULT_TOOL) -> tuple:
    """One timer carrying every chosen time; Persistent catches a sleeping machine."""
    command = " ".join(_quote(part) for part in _invocation(tool))
    service = (
        "[Unit]\n"
        f"Description=acg keepalive: anchor the {tool} usage window\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RuntimeMaxSec=300\n"
        f"ExecStart={command}\n"
    )
    calendars = "".join(f"OnCalendar=*-*-* {at}:00\n" for at in times)
    timer = (
        "[Unit]\n"
        f"Description=acg keepalive ({tool}) at the chosen times\n\n"
        "[Timer]\n"
        f"{calendars}"
        # 機器在那個時間睡著就整天錯位,開機後補跑
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return service, timer


def launchd_plist(times, tool: str = DEFAULT_TOOL) -> bytes:
    import plistlib

    return plistlib.dumps({
        "Label": _LABEL if tool == DEFAULT_TOOL else f"{_LABEL}.{tool}",
        "ProgramArguments": _invocation(tool),
        "StartCalendarInterval": [
            {"Hour": int(at[:2]), "Minute": int(at[3:])} for at in times
        ],
        "RunAtLoad": False,
        "ExitTimeOut": 300,
        "StandardOutPath": str(log_path(tool)),
        "StandardErrorPath": str(log_path(tool)),
    })


def schtasks_argv(times, tool: str = DEFAULT_TOOL) -> list:
    """One task per time: schtasks daily schedules carry a single start time."""
    command = " ".join(_quote(part) for part in _invocation(tool))
    label = _TASK if tool == DEFAULT_TOOL else f"{_TASK} {tool}"
    return [
        [
            "schtasks", "/Create", "/F",
            "/TN", f"{label} {at.replace(':', '')}",
            "/SC", "DAILY", "/ST", at, "/TR", f"cmd /c {command}",
        ]
        for at in times
    ]


def _append_log(message: str, tool: str = DEFAULT_TOOL) -> None:
    path = log_path(tool)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        # 寫不進日誌不該讓這次呼叫失敗:呼叫本身才是重點
        pass


def send(tool: str = DEFAULT_TOOL) -> int:
    """Make the call. A failure is logged and nothing else.

    Missing one costs a window that starts later than intended, which is
    not worth waking anyone for.
    """
    _check_tool(tool)
    settings = load(tool)
    args = run_args(settings, tool)
    _append_log(f"calling {tool}", tool)
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _append_log(f"failed to start: {exc}", tool)
        return 1
    reply = (result.stdout or "").strip().splitlines()
    _append_log(
        f"exit {result.returncode}: {reply[-1] if reply else '(no output)'}", tool,
    )
    return result.returncode


def platform_name() -> str:
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _systemd_dir() -> Path:
    return HOME / ".config" / "systemd" / "user"


def _launchd_path(tool: str = DEFAULT_TOOL) -> Path:
    label = _LABEL if tool == DEFAULT_TOOL else f"{_LABEL}.{tool}"
    return HOME / "Library" / "LaunchAgents" / f"{label}.plist"


def existing_ccs() -> str:
    """Where a claude-scheduler schedule is still installed, or "".

    Both anchor the same window, so leaving the old one running means two
    calls at every time. acg does not remove another tool's schedule; it
    says where it is and stops.
    """
    if platform_name() == "linux":
        found = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=False,
        )
        if "claude-scheduler" in (found.stdout or ""):
            return "crontab(# BEGIN claude-scheduler)"
        return ""
    if platform_name() == "macos":
        legacy = HOME / "Library" / "LaunchAgents"
        hits = sorted(legacy.glob("*claude-scheduler*.plist")) if legacy.is_dir() else []
        return str(hits[0]) if hits else ""
    found = subprocess.run(
        ["schtasks", "/Query", "/FO", "LIST"],
        capture_output=True, text=True, check=False,
    )
    return "ClaudeScheduler_*" if "ClaudeScheduler" in (found.stdout or "") else ""


def _enable_systemd(times, tool: str = DEFAULT_TOOL) -> list:
    directory = _systemd_dir()
    directory.mkdir(parents=True, exist_ok=True)
    unit = unit_name(tool)
    service, timer = systemd_units(times, tool)
    (directory / f"{unit}.service").write_text(service, encoding="utf-8")
    (directory / f"{unit}.timer").write_text(timer, encoding="utf-8")
    lines = [f"寫入 {directory / (unit + '.timer')}"]
    reloaded = subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True, text=True, check=False,
    )
    if reloaded.returncode != 0:
        lines.append("systemctl daemon-reload 失敗,請手動執行")
        return lines
    started = subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{unit}.timer"],
        capture_output=True, text=True, check=False,
    )
    if started.returncode != 0:
        lines.append(f"啟用 timer 失敗:{(started.stderr or '').strip()}")
        return lines
    lines.append(f"已排定每天 {', '.join(times)}")
    lines.append("關機錯過的排程會在開機後補跑")
    return lines


def _enable_launchd(times, tool: str = DEFAULT_TOOL) -> list:
    path = _launchd_path(tool)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launchd_plist(times, tool))
    label = _LABEL if tool == DEFAULT_TOOL else f"{_LABEL}.{tool}"
    target = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{target}/{label}"],
        capture_output=True, check=False, timeout=30,
    )
    loaded = subprocess.run(
        ["launchctl", "bootstrap", target, str(path)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if loaded.returncode != 0:
        return [f"寫入 {path}", f"載入 LaunchAgent 失敗:{(loaded.stderr or '').strip()}"]
    return [f"寫入 {path}", f"已排定每天 {', '.join(times)}"]


def _enable_schtasks(times, tool: str = DEFAULT_TOOL) -> list:
    lines = []
    for argv in schtasks_argv(times, tool):
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            lines.append(f"建立排程失敗:{(done.stderr or '').strip()}")
            return lines
    lines.append(f"已排定每天 {', '.join(times)}")
    return lines


def enable(times=(), replace_ccs: bool = False, tool: str = DEFAULT_TOOL) -> tuple:
    """Install the schedule. Returns (exit code, lines to print)."""
    _check_tool(tool)
    parsed = parse_times(times) if times else Parsed(list(DEFAULT_TIMES))
    if parsed.rejected:
        return 1, [f"看不懂這些時間:{', '.join(parsed.rejected)}", "格式是 HH:MM"]
    if len(parsed.times) > MAX_TIMES:
        return 1, [f"最多 {MAX_TIMES} 個時間,給了 {len(parsed.times)} 個"]

    # ccs 只錨定 Claude 的視窗,別的工具不受它影響
    found = existing_ccs() if tool == DEFAULT_TOOL else ""
    if found and not replace_ccs:
        return 1, [
            f"claude-scheduler 的排程還在:{found}",
            "兩個都開著會在同一時間各點一次火,一天燒兩倍",
            "先用 ccs remove 移除,或加上 --replace-ccs 讓 acg 自己清掉",
        ]
    lines = []
    if found and replace_ccs:
        lines.extend(_remove_ccs())

    settings = load(tool)
    settings.times = tuple(parsed.times)
    save(settings, tool)

    platform = platform_name()
    if platform == "linux":
        lines.extend(_enable_systemd(settings.times, tool))
    elif platform == "macos":
        lines.extend(_enable_launchd(settings.times, tool))
    else:
        lines.extend(_enable_schtasks(settings.times, tool))
    return 0, lines


def _remove_ccs() -> list:
    """Strip claude-scheduler's own schedule, once the user has said to."""
    if platform_name() == "linux":
        current = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=False,
        ).stdout or ""
        kept, skipping = [], False
        for line in current.splitlines():
            if "BEGIN claude-scheduler" in line:
                skipping = True
            elif "END claude-scheduler" in line:
                skipping = False
            elif not skipping and "claude-scheduler" not in line:
                kept.append(line)
        subprocess.run(
            ["crontab", "-"], input="\n".join(kept) + "\n",
            capture_output=True, text=True, check=False,
        )
        return ["已移除 claude-scheduler 的 crontab 排程"]
    return ["請自行移除 claude-scheduler 的排程(acg 只清得掉 crontab 那種)"]


def disable(tool: str = DEFAULT_TOOL) -> tuple:
    """Remove the schedule acg installed. Settings and log stay."""
    _check_tool(tool)
    platform = platform_name()
    unit = unit_name(tool)
    lines = []
    if platform == "linux":
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"{unit}.timer"],
            capture_output=True, check=False,
        )
        for suffix in (".timer", ".service"):
            (_systemd_dir() / f"{unit}{suffix}").unlink(missing_ok=True)
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], capture_output=True, check=False,
        )
    elif platform == "macos":
        subprocess.run(
            ["launchctl", "bootout",
             f"gui/{os.getuid()}/{_LABEL if tool == DEFAULT_TOOL else _LABEL + '.' + tool}"],
            capture_output=True, check=False,
        )
        _launchd_path(tool).unlink(missing_ok=True)
    else:
        label = _TASK if tool == DEFAULT_TOOL else f"{_TASK} {tool}"
        for at in load(tool).times:
            subprocess.run(
                ["schtasks", "/Delete", "/F", "/TN", f"{label} {at.replace(':', '')}"],
                capture_output=True, check=False,
            )
    lines.append(f"已停用 {tool} 的 keepalive 排程")
    lines.append(f"設定與日誌留著:{config_path(tool)}")
    return 0, lines


def installed(tool: str = DEFAULT_TOOL) -> bool:
    _check_tool(tool)
    platform = platform_name()
    if platform == "linux":
        return (_systemd_dir() / f"{unit_name(tool)}.timer").is_file()
    if platform == "macos":
        return _launchd_path(tool).is_file()
    label = _TASK if tool == DEFAULT_TOOL else f"{_TASK} {tool}"
    found = subprocess.run(
        ["schtasks", "/Query", "/FO", "LIST"],
        capture_output=True, text=True, check=False,
    )
    return label in (found.stdout or "")


def last_runs(limit: int = 3, tool: str = DEFAULT_TOOL) -> list:
    try:
        lines = log_path(tool).read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return []
    return lines[-limit:]
