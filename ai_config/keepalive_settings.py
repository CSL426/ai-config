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

This module holds the per-tool settings: times, model, prompt, binary.
"""

import json
import os
from dataclasses import dataclass, field
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
    # exec 是非互動形式;reasoning effort 走 -c,那是 config.toml 的同一個鍵。
    # low 是最低的**有效**值——minimal 讀起來更省,但 API 會回 400 拒絕,
    # 而排程只看得到 exit 1,不會說是哪個參數錯
    "codex": ("codex", (
        "exec", "--skip-git-repo-check",
        "-c", "model_reasoning_effort=low", "{prompt}",
    )),
    # 不指定 --effort:那個旗標只有部分模型收,預設模型換掉就整個呼叫失敗,
    # 而錨定視窗只需要最便宜的回覆,指定強度換不到任何東西
    "agy": ("agy", ("-p", "{prompt}")),
}


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
