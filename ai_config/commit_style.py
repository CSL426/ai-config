"""A commit whose subject ignores this repository's conventions is refused.

The commit-style skill teaches the conventions, but a skill can be passed
over: the model has to remember to read it. This hook does not depend on
remembering. It reads the subject out of the `git commit` about to run and,
when the subject does not match, exits 2 so the reason goes back to the
model, which rewrites the message itself.

Opt-in per machine, like the handoff reminder: the hook entry carries
hooks.COMMIT_STYLE.marker so gather strips it before it reaches the database, and apply
re-adds this machine's own executable. Without that, a hook naming one
machine's interpreter and script path travels to every other machine and
fires there against files that do not exist.
"""

import json
import re
import shlex
import subprocess
import sys

from . import hooks
from .subproc import UTF8

TYPES = ("feat", "fix", "refactor", "docs", "test", "chore", "perf", "ci")
MAX_SUBJECT = 72

_SUBJECT = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9._-]+\))?: .+")
_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1\s*\n(.*?)\n\s*\2\s*$", re.DOTALL | re.MULTILINE,
)


def without_settings(document: dict) -> dict:
    return hooks.without_one(document, hooks.COMMIT_STYLE)


def preserve_settings(source: dict, target: dict) -> dict:
    return hooks.preserve_one(source, target, hooks.COMMIT_STYLE)


def status() -> dict:
    return {"installed": hooks.installed(hooks.read_settings(), hooks.COMMIT_STYLE)}


def configure(enabled: bool) -> dict:
    return {"installed": hooks.configure(hooks.COMMIT_STYLE, enabled)}


def _literal(message: str, command: str) -> "str | None":
    """The message's real first line, or None when it cannot be read.

    A body is written as -m "$(cat <<'EOF' ... EOF)", which shlex cannot
    see into. Pull the heredoc out of the raw command instead. Anything
    else carrying a substitution is unknowable, so it is left alone.
    """
    if "$(" in message or "`" in message:
        found = _HEREDOC.search(command)
        if found is None:
            return None
        return found[3].strip().splitlines()[0].strip()
    if "$" in message:
        return None
    return message.splitlines()[0].strip()


def commit_subject(command: str) -> "str | None":
    """The subject of a `git commit`, or None when this is not one to judge."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if "git" not in words:
        return None
    rest = words[words.index("git") + 1:]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
    if not rest or rest[0] != "commit":
        return None
    flags = rest[1:]
    # -F/--file and templates carry the user's own text; an amend with no
    # new message keeps the old one. None of those are ours to judge.
    for flag in flags:
        if flag in ("-F", "--file", "-t", "--template", "--squash", "--fixup"):
            return None
    if "--amend" in flags and not any(
        f == "-m" or f.startswith(("-m", "--message")) for f in flags
    ):
        return None
    for index, flag in enumerate(flags):
        if flag in ("-m", "--message") and index + 1 < len(flags):
            return _literal(flags[index + 1], command)
        if flag.startswith("--message="):
            return _literal(flag.split("=", 1)[1], command)
        if flag.startswith("-m") and len(flag) > 2:
            return _literal(flag[2:], command)
    return None


def problems(subject: str) -> list[str]:
    found = []
    if not _SUBJECT.match(subject):
        found.append(
            f"主旨要寫成 type(scope): description。可用的 type:{', '.join(TYPES)}。"
        )
    if len(subject) > MAX_SUBJECT:
        found.append(f"主旨 {len(subject)} 字元,超過 {MAX_SUBJECT}。")
    if subject.endswith("."):
        found.append("主旨結尾不要句點。")
    return found


def _recent_subjects(cwd: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", cwd, "log", "--format=%s", "-10"],
            capture_output=True, text=True, **UTF8, timeout=5, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run_hook(args: list[str]) -> int:
    """Hook entry point: exit 2 hands the reason back to the model."""
    if args:
        return 0
    try:
        payload = json.loads(sys.stdin.read(4 * 1024 * 1024))
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0
    subject = commit_subject(command)
    if subject is None:
        return 0
    found = problems(subject)
    if not found:
        return 0

    print(f"這個 commit 主旨不符慣例:{subject}", file=sys.stderr)
    for problem in found:
        print(f"  - {problem}", file=sys.stderr)
    cwd = payload.get("cwd")
    recent = _recent_subjects(cwd) if isinstance(cwd, str) and cwd else ""
    if recent:
        print("\n這個 repo 最近的寫法:", file=sys.stderr)
        for line in recent.splitlines():
            print(f"  {line}", file=sys.stderr)
    print("\n請改寫主旨後重送,不要繞過這個檢查。", file=sys.stderr)
    return 2
