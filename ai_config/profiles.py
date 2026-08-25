"""Named deploy profiles: reusable selections stored in the data repository."""

import re
from pathlib import Path

from .console import log_warn

PROFILES_NAME = "deploy-profiles.toml"

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    tomllib = None  # type: ignore[assignment]

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def profiles_path(source: Path) -> Path:
    return source / PROFILES_NAME


def valid_profile_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def load_profiles(source: Path) -> dict[str, list[str]]:
    """Profile name -> selected item names. Malformed files degrade to {}."""
    path = profiles_path(source)
    if not path.is_file():
        return {}
    if tomllib is None:  # pragma: no cover
        log_warn("Python 3.11+ is required to read deploy profiles; ignoring")
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log_warn(f"Ignoring unreadable {PROFILES_NAME}: {exc}")
        return {}

    profiles: dict[str, list[str]] = {}
    for name, body in raw.items():
        if not valid_profile_name(name):
            log_warn(f"Ignoring unsafe profile name: {name}")
            continue
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or not all(
            isinstance(entry, str) for entry in items
        ):
            log_warn(f"Ignoring profile without a string 'items' list: {name}")
            continue
        profiles[name] = items
    return profiles


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_profile(source: Path, name: str, items: list[str]) -> None:
    """Add or replace one profile, preserving the others."""
    profiles = load_profiles(source)
    profiles[name] = items
    lines = ["# Deploy profiles for `acg deploy --profile <name>`.", ""]
    for key in sorted(profiles):
        lines.append(f"[{key}]")
        rendered = ", ".join(_quote(entry) for entry in profiles[key])
        lines.append(f"items = [{rendered}]")
        lines.append("")
    profiles_path(source).write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )
