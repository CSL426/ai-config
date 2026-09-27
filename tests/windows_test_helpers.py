"""Repositories, homes and script runners shared by the Windows sync tests."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_env(
    home_dir: Path, *, force_copy_fallback: bool = True
) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["XDG_CONFIG_HOME"] = str(home_dir / ".config")
    env["XDG_DATA_HOME"] = str(home_dir / ".local/share")
    env["XDG_CACHE_HOME"] = str(home_dir.parent / ".runtime-cache")
    if force_copy_fallback:
        env["AI_CONFIG_FORCE_COPY_FALLBACK"] = "1"
    return env


def run_script(
    repo_dir: Path,
    home_dir: Path,
    *args: str,
    input_text: str | None = None,
    force_copy_fallback: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "ai_config", *args]
    env = make_env(home_dir, force_copy_fallback=force_copy_fallback)
    env["AI_CONFIG_PLATFORM"] = "windows"
    return subprocess.run(
        command,
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        input=input_text,
        check=False,
    )


def copy_runtime_files(repo_dir: Path) -> None:
    shutil.copytree(REPO_ROOT / "ai_config", repo_dir / "ai_config")


def snapshot_tree(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for path in sorted((root, *root.rglob("*"))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def directory_fingerprint(path: Path) -> str:
    records = []
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(path).as_posix()
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        records.append(f"{relative}\0{digest}")
    records.sort(key=str.casefold)
    return hashlib.sha256("\n".join(records).encode()).hexdigest()


def write_skills_ownership(cli: Path, source: Path, kind: str) -> None:
    entry = {
        "version": 1,
        "path": "skills",
        "source": str(source.absolute()),
        "kind": kind,
    }
    if kind == "directory":
        entry["fingerprint"] = directory_fingerprint(cli / "skills")
    elif kind == "junction":
        entry["target"] = str(source.absolute())
    else:
        raise ValueError(f"Unsupported ownership kind: {kind}")
    state = {"version": 1, "entries": [entry]}
    write(cli / ".ai-config-skills-state.json", json.dumps(state))
    write(cli / ".ai-config-skills-mirror", "skills\n")
