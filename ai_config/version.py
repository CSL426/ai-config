"""Installed package version lookup."""

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


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
