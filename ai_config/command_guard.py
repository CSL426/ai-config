"""Shell commands the user has ruled out are refused before they run.

A rule written into CLAUDE.md is advice: the model has to remember it, and
the failures this guards against were each a sentence there that got passed
over. This PreToolUse hook reads the rules out of the database
(`claude/command-guard.json`) and, when the Bash command about to run starts
one of them, exits 2 so the reason goes back to the model instead.

Only the start of each simple command is matched, after leading
assignments, redirections, `!`, shell keywords (`if`, `then`, `do`...) and
wrappers such as `sudo -u x` or `env -i`. `git commit -m "never run gh auth
switch"` mentions a ruled-out command without running it, and a heredoc body
is text handed to a command, so both are left out. The guard catches
mistakes, not someone set on getting around it: `bash -c "..."`, quoted
`$(...)`, `eval` and a race between two hooks checking `when_running` at the
same moment all pass.

Opt-in per machine through the hook registry, like commit-style: the entry
names this machine's executable and the database it reads, so gather strips
it and apply re-adds the local one.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .subproc import UTF8

RULES_FILE = Path("claude") / "command-guard.json"
MAX_INPUT = 4 * 1024 * 1024

_SEPARATORS = ";&|()\n"
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECTION = re.compile(r"^\d*(?:[<>]|&>)")
# 只有符號、目標在下一個詞的重導向,例如 `2> log`
_BARE_REDIRECTION = re.compile(r"^\d*(?:[<>]{1,2}|&>{1,2}|>&)$")
# 這些只是換個方式執行後面的指令,判斷時要跳過
_WRAPPERS = {"env", "command", "exec", "time", "nohup", "sudo", "nice", "!"}
# 會吃掉下一個詞當參數的 wrapper 選項
_WRAPPER_VALUES = {"sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U"}, "env": {"-u", "-C", "-S"}, "nice": {"-n"}}
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "{"}
# `<<<` 是 here-string,不是 heredoc
_HEREDOC_START = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


@dataclass(frozen=True)
class Rule:
    name: str
    command: re.Pattern
    reason: str
    when_file: str = ""
    when_running: "re.Pattern | None" = None


def load_rules(data_dir: Path) -> "tuple[list[Rule], list[str]]":
    """The usable rules, and why each unusable one was skipped.

    A broken rule must never block every command, so it is dropped and
    reported instead of failing the whole file.
    """
    path = data_dir / RULES_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as exc:
        return [], [f"{path} 讀不了:{exc}"]
    entries = document.get("rules") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return [], [f"{path} 沒有 rules 陣列"]
    rules, problems = [], []
    for index, entry in enumerate(entries):
        label = entry.get("name") if isinstance(entry, dict) else None
        label = label if isinstance(label, str) and label else f"#{index + 1}"
        try:
            rules.append(_rule(entry, label))
        except (TypeError, ValueError, re.error) as exc:
            problems.append(f"規則 {label} 略過:{exc}")
    return rules, problems


def _rule(entry: object, label: str) -> Rule:
    if not isinstance(entry, dict):
        raise TypeError("不是物件")
    command, reason = entry.get("command"), entry.get("reason")
    if not isinstance(command, str) or not command:
        raise ValueError("缺 command")
    if not isinstance(reason, str) or not reason:
        raise ValueError("缺 reason")
    when_file = entry.get("when_file", "")
    when_running = entry.get("when_running", "")
    if not isinstance(when_file, str) or not isinstance(when_running, str):
        raise TypeError("when_file / when_running 要是字串")
    if when_file and not _contained_name(when_file):
        raise ValueError(f"when_file 要是相對檔名,不能往外走:{when_file}")
    return Rule(
        name=label, command=re.compile(command), reason=reason,
        when_file=when_file,
        when_running=re.compile(when_running) if when_running else None,
    )


def _without_heredoc_bodies(command: str) -> str:
    """Drop every heredoc body: it is data handed to a command, not commands."""
    lines = command.split("\n")
    kept, waiting = [], []
    for line in lines:
        if waiting:
            if line.strip() == waiting[0]:
                waiting.pop(0)
            continue
        kept.append(line)
        waiting.extend(found[2] for found in _HEREDOC_START.finditer(line))
    return "\n".join(kept)


def simple_commands(command: str) -> "list[tuple[int, list[str]]] | None":
    """Each simple command's subshell depth and words, or None when unreadable."""
    lexer = shlex.shlex(
        _without_heredoc_bodies(command), posix=True, punctuation_chars=_SEPARATORS,
    )
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    found: list[tuple[int, list[str]]] = []
    words: list[str] = []
    depth = 0
    for token in tokens:
        if token and set(token) <= set(_SEPARATORS):
            if words:
                found.append((depth, words))
            words = []
            depth += token.count("(") - token.count(")")
            depth = max(depth, 0)
        else:
            words.append(token)
    if words:
        found.append((depth, words))
    return found


def _effective(words: list[str]) -> list[str]:
    """The words from the command actually being run onward."""
    rest = list(words)
    while rest:
        head = rest[0]
        if _ASSIGNMENT.match(head) or _REDIRECTION.match(head) or head in _KEYWORDS:
            rest.pop(0)
            if _BARE_REDIRECTION.match(head) and rest:
                rest.pop(0)
            continue
        if head in _WRAPPERS:
            rest.pop(0)
            takes_value = _WRAPPER_VALUES.get(head, set())
            while rest and rest[0].startswith("-"):
                option = rest.pop(0)
                if option in takes_value and rest:
                    rest.pop(0)
            continue
        break
    return rest


def _contained_name(name: str) -> bool:
    """when_file names a file beside the command, never a path out of it."""
    pure = Path(name)
    return bool(name) and not pure.is_absolute() and ".." not in pure.parts


def _has_file_upward(start: Path, name: str) -> bool:
    """`name` is a file in start or a parent, stopping at the repository root."""
    for directory in (start, *start.parents):
        if (directory / name).is_file():
            return True
        if (directory / ".git").exists():
            return False
    return False


def _native(path: str) -> str:
    """Git Bash spells C:\\x as /c/x; Python on Windows reads that as C:\\c\\x."""
    if os.name == "nt":
        found = re.match(r"^/([A-Za-z])(/|$)", path)
        if found:
            return f"{found[1]}:/{path[found.end():]}"
    return path


def _running(pattern: re.Pattern, problems: "list[str] | None" = None) -> bool:
    """Another process's command line matches, not counting our own ancestry.

    A shell that launched this check carries the checked text in its own
    arguments, and would otherwise always count as already running.
    """
    if os.name == "nt":
        # Windows 沒有便宜又可靠的方式列出完整指令列;寧可放行也不要誤擋
        return False
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, **UTF8,
            timeout=3, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        if problems is not None:
            problems.append(f"列不出行程,when_running 規則這次沒有作用:{exc}")
        return False
    parents, commands = {}, {}
    for line in listing.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            pid = int(fields[0])
            parents[pid], commands[pid] = int(fields[1]), fields[2]
    ours, pid = set(), os.getpid()
    while pid and pid not in ours:
        ours.add(pid)
        pid = parents.get(pid, 0)
    return any(
        pattern.search(command) for pid, command in commands.items() if pid not in ours
    )


def violations(
    command: str, cwd: Path, rules: list[Rule], problems: "list[str] | None" = None,
) -> list[tuple[Rule, str]]:
    """(rule, the simple command it matched) for each rule the command breaks."""
    parsed = simple_commands(command)
    if not parsed or not rules:
        return []
    found = []
    # 子 shell 裡的 cd 出了括號就不算
    here = {0: cwd}
    for depth, words in parsed:
        base = here.get(depth) or here[max(d for d in here if d <= depth)]
        here = {d: p for d, p in here.items() if d <= depth}
        here[depth] = base
        effective = _effective(words)
        if not effective:
            continue
        if effective[0] == "cd" and len(effective) == 2:
            target = effective[1]
            # 有變數展開就不知道會到哪,維持原目錄
            if "$" not in target and "`" not in target:
                here[depth] = (base / os.path.expanduser(_native(target))).resolve()
            continue
        text = " ".join(effective)
        for rule in rules:
            if not rule.command.match(text):
                continue
            if rule.when_file and not _has_file_upward(here[depth], rule.when_file):
                continue
            if rule.when_running is not None and not _running(rule.when_running, problems):
                continue
            found.append((rule, text))
    return found


def _warn(problems: list[str]) -> None:
    """Shown to the person: a rule that cannot work should not look active.

    Exit 0 stderr appears only in verbose mode, so the warning goes out as
    the hook's systemMessage instead.
    """
    if problems:
        message = "command-guard:" + ";".join(problems)
        print(json.dumps({"systemMessage": message}, ensure_ascii=False))


def run_hook(args: list[str]) -> int:
    """Hook entry point: exit 2 hands the reason back to the model."""
    if len(args) != 1:
        return 0
    try:
        payload = json.loads(sys.stdin.read(MAX_INPUT))
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    cwd = payload.get("cwd")
    if not isinstance(command, str) or not isinstance(cwd, str) or not cwd:
        return 0
    rules, problems = load_rules(Path(args[0]))
    found = violations(command, Path(cwd), rules, problems)
    if not found:
        _warn(problems)
        return 0
    for rule, text in found:
        print(f"這個指令被 command-guard 的規則「{rule.name}」擋下:{text}", file=sys.stderr)
        print(f"  {rule.reason}", file=sys.stderr)
    print("\n請改用規則說的做法,不要繞過這個檢查。", file=sys.stderr)
    return 2
