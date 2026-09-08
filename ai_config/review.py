"""Content-based review identities; reads never refresh the Git index."""

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from .paths import EXCLUDED_FILES
from .safety import is_reparse_point


def node(path: Path) -> dict:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "missing"}
    if is_reparse_point(path):
        return {
            "kind": "symlink" if path.is_symlink() else "junction",
            "target": os.readlink(path),
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
    return digest({
        "paths": {str(path): tree(path) for path in dict.fromkeys(paths)},
        "values": values,
    })


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
        result[key] = f"{completed.returncode}:" + hashlib.sha256(
            completed.stdout
        ).hexdigest()
    return result
