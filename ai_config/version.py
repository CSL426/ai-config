"""Installed package version and build identity lookup."""

import re
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ._build import COMMIT_SHA


def current_commit() -> str | None:
    """Identify this build without requiring Git on standalone installations."""
    if not getattr(sys, "frozen", False):
        source = Path(__file__).resolve().parents[1]
        # Only inspect our checkout; installed packages must not inherit the
        # identity of an unrelated repository containing the environment.
        if (source / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "-C", str(source), "rev-parse", "--verify", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            else:
                commit = result.stdout.strip()
                if result.returncode == 0 and re.fullmatch(
                    r"[0-9a-f]{40}|[0-9a-f]{64}", commit
                ):
                    return commit
    return COMMIT_SHA or None


def _source_version() -> "str | None":
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        with pyproject.open("rb") as file:
            document = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = document.get("project")
    if not isinstance(project, dict):
        return None
    value = project.get("version")
    return value if isinstance(value, str) else None


def current_version() -> "str | None":
    if not getattr(sys, "frozen", False):
        source = _source_version()
        if source is not None:
            return source
    try:
        return version("ai-config")
    except PackageNotFoundError:
        return None
