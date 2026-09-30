"""Installing the keepalive with the platform scheduler: systemd, launchd, schtasks."""

import os
import subprocess
import sys
from pathlib import Path

from . import keepalive_settings
from .autopush import windows_command
from .subproc import NATIVE, UTF8
from .systemd_timer import forget_missed_runs

_UNIT = "acg-keepalive"
_LABEL = "com.csl426.acg.keepalive"
_TASK = "acg keepalive"


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def _invocation(tool: str = keepalive_settings.DEFAULT_TOOL) -> list:
    from . import paths

    suffix = [] if tool == keepalive_settings.DEFAULT_TOOL else [tool]
    return [*paths.scheduled_command(), "keepalive", "send", *suffix]


def unit_name(tool: str = keepalive_settings.DEFAULT_TOOL) -> str:
    return _UNIT if tool == keepalive_settings.DEFAULT_TOOL else f"{_UNIT}-{tool}"


def systemd_units(times, tool: str = keepalive_settings.DEFAULT_TOOL) -> tuple:
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


def launchd_plist(times, tool: str = keepalive_settings.DEFAULT_TOOL) -> bytes:
    import plistlib

    return plistlib.dumps({
        "Label": _LABEL if tool == keepalive_settings.DEFAULT_TOOL else f"{_LABEL}.{tool}",
        "ProgramArguments": _invocation(tool),
        "StartCalendarInterval": [
            {"Hour": int(at[:2]), "Minute": int(at[3:])} for at in times
        ],
        "RunAtLoad": False,
        "ExitTimeOut": 300,
        "StandardOutPath": str(keepalive_settings.log_path(tool)),
        "StandardErrorPath": str(keepalive_settings.log_path(tool)),
    })


def schtasks_argv(times, tool: str = keepalive_settings.DEFAULT_TOOL) -> list:
    """One task per time: schtasks daily schedules carry a single start time."""
    command = " ".join(_quote(part) for part in _invocation(tool))
    label = _TASK if tool == keepalive_settings.DEFAULT_TOOL else f"{_TASK} {tool}"
    return [
        [
            "schtasks", "/Create", "/F",
            "/TN", f"{label} {at.replace(':', '')}",
            "/SC", "DAILY", "/ST", at, "/TR", windows_command(command),
        ]
        for at in times
    ]


def platform_name() -> str:
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _systemd_dir() -> Path:
    return keepalive_settings.HOME / ".config" / "systemd" / "user"


def _launchd_path(tool: str = keepalive_settings.DEFAULT_TOOL) -> Path:
    label = _LABEL if tool == keepalive_settings.DEFAULT_TOOL else f"{_LABEL}.{tool}"
    return keepalive_settings.HOME / "Library" / "LaunchAgents" / f"{label}.plist"


def existing_ccs() -> str:
    """Where a claude-scheduler schedule is still installed, or "".

    Both anchor the same window, so leaving the old one running means two
    calls at every time. acg does not remove another tool's schedule; it
    says where it is and stops.
    """
    if platform_name() == "linux":
        found = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, **UTF8, check=False,
        )
        if "claude-scheduler" in (found.stdout or ""):
            return "crontab(# BEGIN claude-scheduler)"
        return ""
    if platform_name() == "macos":
        legacy = keepalive_settings.HOME / "Library" / "LaunchAgents"
        hits = sorted(legacy.glob("*claude-scheduler*.plist")) if legacy.is_dir() else []
        return str(hits[0]) if hits else ""
    found = subprocess.run(
        ["schtasks", "/Query", "/FO", "LIST"],
        capture_output=True, text=True, **NATIVE, check=False,
    )
    return "ClaudeScheduler_*" if "ClaudeScheduler" in (found.stdout or "") else ""


def _enable_systemd(times, tool: str = keepalive_settings.DEFAULT_TOOL) -> list:
    directory = _systemd_dir()
    directory.mkdir(parents=True, exist_ok=True)
    unit = unit_name(tool)
    service, timer = systemd_units(times, tool)
    (directory / f"{unit}.service").write_text(service, encoding="utf-8")
    (directory / f"{unit}.timer").write_text(timer, encoding="utf-8")
    lines = [f"寫入 {directory / (unit + '.timer')}"]
    forget_missed_runs(f"{unit}.timer")
    reloaded = subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True, text=True, **UTF8, check=False,
    )
    if reloaded.returncode != 0:
        lines.append("systemctl daemon-reload 失敗,請手動執行")
        return lines
    started = subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{unit}.timer"],
        capture_output=True, text=True, **UTF8, check=False,
    )
    if started.returncode != 0:
        lines.append(f"啟用 timer 失敗:{(started.stderr or '').strip()}")
        return lines
    lines.append(f"已排定每天 {', '.join(times)}")
    lines.append("關機錯過的排程會在開機後補跑")
    return lines


def _enable_launchd(times, tool: str = keepalive_settings.DEFAULT_TOOL) -> list:
    path = _launchd_path(tool)
    path.parent.mkdir(parents=True, exist_ok=True)
    keepalive_settings.log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launchd_plist(times, tool))
    label = _LABEL if tool == keepalive_settings.DEFAULT_TOOL else f"{_LABEL}.{tool}"
    target = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{target}/{label}"],
        capture_output=True, check=False, timeout=30,
    )
    loaded = subprocess.run(
        ["launchctl", "bootstrap", target, str(path)],
        capture_output=True, text=True, **UTF8, check=False, timeout=30,
    )
    if loaded.returncode != 0:
        return [f"寫入 {path}", f"載入 LaunchAgent 失敗:{(loaded.stderr or '').strip()}"]
    return [f"寫入 {path}", f"已排定每天 {', '.join(times)}"]


def _enable_schtasks(times, tool: str = keepalive_settings.DEFAULT_TOOL) -> list:
    lines = []
    for argv in schtasks_argv(times, tool):
        done = subprocess.run(argv, capture_output=True, text=True, **NATIVE, check=False)
        if done.returncode != 0:
            lines.append(f"建立排程失敗:{(done.stderr or '').strip()}")
            return lines
    lines.append(f"已排定每天 {', '.join(times)}")
    return lines


def enable(times=(), replace_ccs: bool = False, tool: str = keepalive_settings.DEFAULT_TOOL) -> tuple:
    """Install the schedule. Returns (exit code, lines to print)."""
    keepalive_settings._check_tool(tool)
    parsed = keepalive_settings.parse_times(times) if times else keepalive_settings.Parsed(list(keepalive_settings.DEFAULT_TIMES))
    if parsed.rejected:
        return 1, [f"看不懂這些時間:{', '.join(parsed.rejected)}", "格式是 HH:MM"]
    if len(parsed.times) > keepalive_settings.MAX_TIMES:
        return 1, [f"最多 {keepalive_settings.MAX_TIMES} 個時間,給了 {len(parsed.times)} 個"]

    # ccs 只錨定 Claude 的視窗,別的工具不受它影響
    found = existing_ccs() if tool == keepalive_settings.DEFAULT_TOOL else ""
    if found and not replace_ccs:
        return 1, [
            f"claude-scheduler 的排程還在:{found}",
            "兩個都開著會在同一時間各點一次火,一天燒兩倍",
            "先用 ccs remove 移除,或加上 --replace-ccs 讓 acg 自己清掉",
        ]
    lines = []
    if found and replace_ccs:
        lines.extend(_remove_ccs())

    settings = keepalive_settings.load(tool)
    settings.times = tuple(parsed.times)
    keepalive_settings.save(settings, tool)

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
            ["crontab", "-l"], capture_output=True, text=True, **UTF8, check=False,
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
            capture_output=True, text=True, **UTF8, check=False,
        )
        return ["已移除 claude-scheduler 的 crontab 排程"]
    return ["請自行移除 claude-scheduler 的排程(acg 只清得掉 crontab 那種)"]


def disable(tool: str = keepalive_settings.DEFAULT_TOOL) -> tuple:
    """Remove the schedule acg installed. Settings and log stay."""
    keepalive_settings._check_tool(tool)
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
             f"gui/{os.getuid()}/{_LABEL if tool == keepalive_settings.DEFAULT_TOOL else _LABEL + '.' + tool}"],
            capture_output=True, check=False,
        )
        _launchd_path(tool).unlink(missing_ok=True)
    else:
        label = _TASK if tool == keepalive_settings.DEFAULT_TOOL else f"{_TASK} {tool}"
        for at in keepalive_settings.load(tool).times:
            subprocess.run(
                ["schtasks", "/Delete", "/F", "/TN", f"{label} {at.replace(':', '')}"],
                capture_output=True, check=False,
            )
    lines.append(f"已停用 {tool} 的 keepalive 排程")
    lines.append(f"設定與日誌留著:{keepalive_settings.config_path(tool)}")
    return 0, lines


def installed(tool: str = keepalive_settings.DEFAULT_TOOL) -> bool:
    keepalive_settings._check_tool(tool)
    platform = platform_name()
    if platform == "linux":
        return (_systemd_dir() / f"{unit_name(tool)}.timer").is_file()
    if platform == "macos":
        return _launchd_path(tool).is_file()
    label = _TASK if tool == keepalive_settings.DEFAULT_TOOL else f"{_TASK} {tool}"
    found = subprocess.run(
        ["schtasks", "/Query", "/FO", "LIST"],
        capture_output=True, text=True, **NATIVE, check=False,
    )
    return label in (found.stdout or "")


def refresh_windows_tasks() -> list:
    """Rewrite keepalive tasks made before they ran headless; lines to report."""
    if platform_name() != "windows":
        return []
    lines = []
    for tool in keepalive_settings.TOOLS:
        times = keepalive_settings.load(tool).times
        if not times or not installed(tool):
            continue
        label = _TASK if tool == keepalive_settings.DEFAULT_TOOL else f"{_TASK} {tool}"
        listed = subprocess.run(
            ["schtasks", "/Query", "/TN", f"{label} {times[0].replace(':', '')}", "/XML"],
            capture_output=True, text=True, **NATIVE, check=False,
        )
        if listed.returncode != 0 or "--headless" in listed.stdout:
            continue
        code, _ = enable(times, tool=tool)
        if code == 0:
            lines.append(f"{tool} 的 keepalive 排程改成不開視窗執行")
    return lines
