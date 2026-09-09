"""Content-based review identities; reads never refresh the Git index."""

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from .paths import EXCLUDED_FILES
from .safety import is_reparse_point


def strip_extended_prefix(text: str) -> str:
    """Drop Windows' ``\\\\?\\`` form so a Junction target compares like a path.

    os.readlink reports Junction targets in the extended-length form. Left
    alone, the target is no longer relative to the home it lives in and
    every review of that home fails as "link outside managed home".
    """
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith(("\\\\?\\", "\\??\\")):
        return text[4:]
    return text


def link_target(path: Path) -> str:
    return strip_extended_prefix(os.readlink(path))


def node(path: Path) -> dict:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "missing"}
    if is_reparse_point(path):
        return {
            "kind": "symlink" if path.is_symlink() else "junction",
            "target": link_target(path),
        }
    if stat.S_ISDIR(info.st_mode):
        return {"kind": "directory"}
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"Unsupported filesystem object: {path}")
    return {
        "kind": "file",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mode": stat.S_IMODE(info.st_mode),
    }


def tree(root: Path) -> dict[str, dict]:
    result = {str(root): node(root)}
    if result[str(root)]["kind"] == "directory":
        for child in sorted(root.iterdir()):
            if child.name in EXCLUDED_FILES or child.name == ".git":
                continue
            result.update(tree(child))
    return result


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def fingerprint(paths: list[Path], values: object = None) -> str:
    return digest(
        {
            "paths": {str(path): tree(path) for path in dict.fromkeys(paths)},
            "values": values,
        }
    )


def git_state(repo: Path) -> dict[str, str]:
    result = {}
    commands = {
        "head": ["rev-parse", "HEAD"],
        "branch": ["symbolic-ref", "-q", "HEAD"],
        "upstream": ["rev-parse", "@{upstream}"],
        "status": ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        "diff": ["diff", "--binary", "HEAD"],
    }
    for key, args in commands.items():
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            timeout=30,
            check=False,
        )
        result[key] = (
            f"{completed.returncode}:" + hashlib.sha256(completed.stdout).hexdigest()
        )
    return result
